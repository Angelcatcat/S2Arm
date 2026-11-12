// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 S2Arm contributors
using System;
using System.Buffers;
using System.IO;
using System.IO.Compression;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace S2A.Services
{
    /// <summary>RomFS 单文件镜像的来源：明文 romfs.img 直读，或分块 gzip 的 romfs.img.gz（pack/phone_pack/gzimage.py）。
    /// 上层 <see cref="RomFsIndex"/> 只认这个接口，两种形态读出来的字节必须完全一样。</summary>
    internal interface IRomFsImage : IDisposable
    {
        /// <summary>解压后的逻辑长度（对照表 romfs.imgmap 记的就是它）。</summary>
        long Length { get; }

        /// <summary>读 destination.Length 字节；返回实际读到的字节数（0 = 界内却读不到）。可能抛 IOException(EFAULT)。</summary>
        int Read(Span<byte> destination, long offset);

        /// <summary>EFAULT（目标是写保护的游戏内存页）时改中转读：先报一次写再拷。压缩镜像读的是宿主缓冲，不需要。</summary>
        bool ReadBounce(Span<byte> destination, long offset, out int got);

        /// <summary>日志 / 报错里用的名字。</summary>
        string Label { get; }
    }

    /// <summary>明文 romfs.img：RandomAccess 直读，EFAULT 走 LazyHostFile.ReadViaBounce（#602）。</summary>
    internal sealed class PlainRomFsImage : IRomFsImage
    {
        private readonly SafeFileHandle _handle;
        private readonly string _label;

        public PlainRomFsImage(SafeFileHandle handle, string label)
        {
            _handle = handle;
            _label = label;
        }

        public long Length => RandomAccess.GetLength(_handle);

        public string Label => _label;

        public int Read(Span<byte> destination, long offset) => RandomAccess.Read(_handle, destination, offset);

        public bool ReadBounce(Span<byte> destination, long offset, out int got)
        {
            if (!LazyHostFile.ReadViaBounce(_handle, destination, offset, _label, out long read)) { got = 0; return false; }
            got = (int)read;
            return true;
        }

        public void Dispose() => _handle.Dispose();
    }

    /// <summary>分块 gzip 镜像 romfs.img.gz：整体 gzip 不能随机读，这里每块一个独立 gzip member，读到哪块解哪块。
    /// 布局见 pack/phone_pack/gzimage.py 的注释（小端：魔数 8 + 逻辑长度 8 + 块大小 4 + 块数 4 + 索引偏移 8，
    /// 数据区按块序，末尾 blockCount × {u64 偏移; u32 压缩长; u32 明文长}）。</summary>
    internal sealed class RomFsImageGz : IRomFsImage
    {
        public const string Suffix = ".gz";
        private static ReadOnlySpan<byte> Magic => "S2AGZ01\0"u8;
        private const int HeaderBytes = 32;
        private const int EntryBytes = 16;
        private const int CacheBlocks = 8;   // 环形的最近 8 块（默认 1 MiB 一块 = 8 MiB）：游戏连着读同一片区域时不重复解压

        private readonly SafeFileHandle _handle;
        private readonly string _path;
        private readonly long _logical;
        private readonly int _blockSize;
        private readonly int _blocks;
        private readonly long _indexOffset;
        private readonly long[] _dataOffset;
        private readonly int[] _packedLen;
        private readonly int[] _rawLen;
        private readonly byte[][] _cache = new byte[CacheBlocks][];
        private readonly long[] _cacheBlock = new long[CacheBlocks];
        private readonly object _lock = new();
        private int _next;

        private RomFsImageGz(SafeFileHandle handle, string path, long logical, int blockSize, int blocks, long indexOffset)
        {
            _handle = handle;
            _path = path;
            _logical = logical;
            _blockSize = blockSize;
            _blocks = blocks;
            _indexOffset = indexOffset;
            _dataOffset = new long[blocks];
            _packedLen = new int[blocks];
            _rawLen = new int[blocks];
            Array.Fill(_cacheBlock, -1L);
        }

        /// <summary>开一份压缩镜像；文件不在、魔数不对、索引读不全都返回 null（调用方退回明文镜像或报缺）。</summary>
        public static RomFsImageGz Open(string path)
        {
            if (!File.Exists(path)) return null;
            SafeFileHandle handle = null;
            try
            {
                handle = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.Read, FileOptions.RandomAccess);
                Span<byte> head = stackalloc byte[HeaderBytes];
                if (ReadExactly(handle, head, 0) != HeaderBytes || !head[..8].SequenceEqual(Magic)) throw new InvalidDataException();
                long logical = BitConverter.ToInt64(head[8..]);
                int blockSize = BitConverter.ToInt32(head[16..]);
                int blocks = BitConverter.ToInt32(head[20..]);
                long indexOffset = BitConverter.ToInt64(head[24..]);
                long fileLength = RandomAccess.GetLength(handle);
                // 自洽性：块大小为正、块数对得上逻辑长度、索引表落在文件里且长度够
                if (blockSize <= 0 || logical < 0 || blocks < 0 ||
                    (long)blockSize * blocks < logical || (blocks > 0 && (long)blockSize * (blocks - 1) >= logical) ||
                    indexOffset < HeaderBytes || indexOffset + (long)EntryBytes * blocks > fileLength)
                    throw new InvalidDataException();
                RomFsImageGz image = new(handle, path, logical, blockSize, blocks, indexOffset);
                byte[] table = new byte[EntryBytes * blocks];
                if (ReadExactly(handle, table, indexOffset) != table.Length) throw new InvalidDataException();
                for (int i = 0; i < blocks; i++)
                {
                    int at = i * EntryBytes;
                    image._dataOffset[i] = BitConverter.ToInt64(table, at);
                    image._packedLen[i] = BitConverter.ToInt32(table, at + 8);
                    image._rawLen[i] = BitConverter.ToInt32(table, at + 12);
                    if (image._dataOffset[i] < HeaderBytes || image._packedLen[i] <= 0 ||
                        image._dataOffset[i] + image._packedLen[i] > indexOffset ||
                        image._rawLen[i] <= 0 || image._rawLen[i] > blockSize)
                        throw new InvalidDataException();
                }
                handle = null;   // 交给实例管
                return image;
            }
            catch (Exception error) when (error is InvalidDataException or IOException or ArgumentException or NotSupportedException or UnauthorizedAccessException)
            {
                Console.Error.WriteLine($"[fs] {path} 不是可用的分块 gzip 镜像（{error.GetType().Name}）：{error.Message}");
                return null;
            }
            finally
            {
                handle?.Dispose();
            }
        }

        public long Length => _logical;

        public string Label => Path.GetFileName(_path);

        public int Read(Span<byte> destination, long offset)
        {
            if (offset < 0 || offset > _logical) return 0;
            int done = 0;
            while (done < destination.Length)
            {
                long at = offset + done;
                long which = at / _blockSize;
                if (which >= _blocks) break;
                byte[] raw = Block((int)which);
                int inner = (int)(at - which * _blockSize);
                int take = Math.Min(raw.Length - inner, destination.Length - done);
                if (take <= 0) break;
                CopyToGuest(raw.AsSpan(inner, take), destination.Slice(done, take));
                done += take;
            }
            return done;
        }

        // 压缩镜像读出来的数据本来就在宿主缓冲里，直接拷进目标即可；但目标若是写保护的游戏内存页（#602），
        // 得先让写跟踪解一次保护再拷，否则 memcpy 直接踩保护页把进程带走
        private static unsafe void CopyToGuest(ReadOnlySpan<byte> source, Span<byte> destination)
        {
            if (destination.IsEmpty) return;
            ulong host = (ulong)Unsafe.AsPointer(ref MemoryMarshal.GetReference(destination));
            if (GuestMemory.TryGuestAddress(host, (ulong)destination.Length, out ulong va) && GuestMemory.WriteHook != null)
                GuestMemory.WriteHook(va, (ulong)destination.Length);
            source.CopyTo(destination);
        }

        public bool ReadBounce(Span<byte> destination, long offset, out int got)
        {
            got = Read(destination, offset);   // 已经是宿主缓冲再拷，没有额外的中转可走
            return got == destination.Length;
        }

        private byte[] Block(int index)
        {
            lock (_lock)
            {
                for (int i = 0; i < CacheBlocks; i++)
                    if (_cacheBlock[i] == index && _cache[i] != null) return _cache[i];
            }

            byte[] packed = ArrayPool<byte>.Shared.Rent(_packedLen[index]);
            byte[] raw = null;
            try
            {
                int read = ReadExactly(_handle, packed.AsSpan(0, _packedLen[index]), _dataOffset[index]);
                if (read != _packedLen[index]) throw new InvalidDataException($"{Label} 第 {index} 块只读到 {read} / {_packedLen[index]} 字节");
                raw = new byte[_rawLen[index]];
                using MemoryStream input = new(packed, 0, _packedLen[index], writable: false);
                using GZipStream gz = new(input, CompressionMode.Decompress);
                int got = 0;
                while (got < raw.Length)
                {
                    int n = gz.Read(raw, got, raw.Length - got);
                    if (n <= 0) break;
                    got += n;
                }
                if (got != raw.Length) throw new InvalidDataException($"{Label} 第 {index} 块解出 {got} / {raw.Length} 字节");
            }
            finally
            {
                ArrayPool<byte>.Shared.Return(packed);
            }

            lock (_lock)
            {
                int slot = _next;
                _next = (_next + 1) % CacheBlocks;
                _cache[slot] = raw;
                _cacheBlock[slot] = index;
            }
            return raw;
        }

        private static int ReadExactly(SafeFileHandle handle, Span<byte> destination, long offset)
        {
            int done = 0;
            while (done < destination.Length)
            {
                int n = RandomAccess.Read(handle, destination[done..], offset + done);
                if (n <= 0) break;
                done += n;
            }
            return done;
        }

        public void Dispose() => _handle.Dispose();
    }
}
