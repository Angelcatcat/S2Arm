#!/usr/bin/env python3
"""分块 gzip 镜像（romfs.img.gz）：给手机端省体积，还能随机读。

为什么不是整个 gzip：RomFS 镜像是随机访问的（游戏按偏移读几千个小文件），一个 gzip 流必须从头解，
读一次末尾就要解 2 GB。这里把镜像切成固定大小的块、每块一个独立 gzip member（各自 4 字节头 +
deflate + CRC），再在文件尾放一张索引表记每块的位置和长度 —— 读哪块只解哪块。

布局（全部小端）：
    0    "S2AGZ01\\0"          8     魔数
    8    u64 logicalLength     8     解压后镜像的总长度
    16   u32 blockSize         4     每块明文字节数（最后一块可能更短）
    20   u32 blockCount        4
    24   u64 indexOffset       8     索引表偏移
    ...  数据区：blockCount 个 gzip member，按块序连续
    ...  索引表：blockCount × {u64 dataOffset; u32 packedLength; u32 rawLength}

压缩侧用 zlib（Python 自带、玩家机一定有；iOS 侧 .NET 用 GZipStream 解，同样零新增依赖）。
读取方：services/S2A.Services/RomFsImageGz.cs（手机/桌面同一份 C# 核心）、本文件的 read_at（工具与自测）。
"""
import hashlib
import os
import struct
import zlib

MAGIC = b"S2AGZ01\0"
HEADER = 32                 # 头长度
ENTRY = struct.Struct("<QII")   # 索引项：数据偏移、压缩后长度、解压后长度
BLOCK = 1 << 20             # 每块 1 MiB：iPhone 上解一块约几毫秒，太小索引膨胀、太大读一次解太多
SUFFIX = ".gz"


class GzImageError(ValueError):
    pass


def blob_name(name):
    """romfs.img → romfs.img.gz（别的名字也照加后缀）。"""
    return name + SUFFIX


def pack_image(src, dst, block=BLOCK, level=6, tick=None):
    """把 src 分块压成 dst。先写 .part 再改名，中断不留半份。返回统计。"""
    size = os.path.getsize(src)
    block = max(int(block), 1)
    count = (size + block - 1) // block
    index_off = HEADER
    rows = []
    tmp = dst + ".part"
    h = hashlib.sha256()
    with open(src, "rb") as f, open(tmp, "wb") as out:
        out.write(MAGIC + struct.pack("<QIIQ", size, block, count, 0))   # indexOffset 最后回填
        for i in range(count):
            chunk = f.read(block)
            if not chunk:
                raise GzImageError("%s 读到第 %d 块就没数据了（文件在读的过程中变短？）" % (src, i))
            h.update(chunk)
            co = zlib.compressobj(level, zlib.DEFLATED, 31)   # wbits=31：gzip member
            packed = co.compress(chunk) + co.flush()
            rows.append((index_off, len(packed), len(chunk)))
            out.write(packed)
            index_off += len(packed)
            if tick:
                tick(i + 1, count)
        out.write(b"".join(ENTRY.pack(*r) for r in rows))
        out.seek(24)
        out.write(struct.pack("<Q", index_off))
    os.replace(tmp, dst)
    return dict(logicalBytes=size, blockSize=block, blocks=count,
                packedBytes=index_off + count * ENTRY.size, sha256=h.hexdigest())


def header(path):
    """读容器头；不是压缩镜像就抛 GzImageError（调用方据此退回明文镜像）。"""
    with open(path, "rb") as f:
        head = f.read(HEADER)
    if len(head) != HEADER or head[:8] != MAGIC:
        raise GzImageError("%s 不是分块 gzip 镜像" % path)
    logical, block, count, index_off = struct.unpack("<QIIQ", head[8:])
    if block <= 0 or logical < 0 or block * (count - 1) >= max(logical, 1) + block:
        raise GzImageError("%s 的头不自洽：长度 %d、块 %d、%d 块" % (path, logical, block, count))
    return dict(logicalBytes=logical, blockSize=block, blocks=count, indexOffset=index_off)


def read_at(path, offset, length, cache=None):
    """按偏移读（工具 / 自测用；手机端是 C# 的 RomfsImageGz）。cache 传 dict 可复用已解的块。"""
    info = header(path)
    if offset < 0 or length < 0 or offset + length > info["logicalBytes"]:
        raise GzImageError("读越界：%d + %d > %d" % (offset, length, info["logicalBytes"]))
    block = info["blockSize"]
    with open(path, "rb") as f:
        out = bytearray()
        pos, end = offset, offset + length
        while pos < end:
            i = pos // block
            key = i
            raw = None if cache is None else cache.get(key)
            if raw is None:
                f.seek(info["indexOffset"] + i * ENTRY.size)
                data_off, packed_len, raw_len = ENTRY.unpack(f.read(ENTRY.size))
                f.seek(data_off)
                raw = zlib.decompressobj(31).decompress(f.read(packed_len))
                if len(raw) != raw_len:
                    raise GzImageError("%s 第 %d 块解出 %d 字节，索引记 %d" % (path, i, len(raw), raw_len))
                if cache is not None:
                    cache[key] = raw
            inner = pos - i * block
            take = min(len(raw) - inner, end - pos)
            if take <= 0:
                raise GzImageError("%s 第 %d 块长度不足，读到 %d 就没了" % (path, i, pos))
            out += raw[inner:inner + take]
            pos += take
    return bytes(out)


def verify(path):
    """逐块解一遍，核总长度与每块长度；返回 (logicalBytes, sha256)。坏块抛 GzImageError。"""
    info = header(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for i in range(info["blocks"]):
            f.seek(info["indexOffset"] + i * ENTRY.size)
            data_off, packed_len, raw_len = ENTRY.unpack(f.read(ENTRY.size))
            f.seek(data_off)
            raw = zlib.decompressobj(31).decompress(f.read(packed_len))
            if len(raw) != raw_len:
                raise GzImageError("第 %d 块解出 %d 字节，索引记 %d" % (i, len(raw), raw_len))
            h.update(raw)
    return info["logicalBytes"], h.hexdigest()
