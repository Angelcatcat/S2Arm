// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 S2Arm contributors
using LibHac;
using LibHac.Fs;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using Path = System.IO.Path;

namespace S2A.Services.Tests
{
    // 分块 gzip 镜像（玩家生成手机资源包时选「压缩」：机上只有 romfs.img.gz、没有 romfs.img）。
    //   格式由 pack/phone_pack/gzimage.py 写、这里读；对照表 romfs.imgmap 记的仍是解压后的逻辑长度。
    //   要点：整份读出来的字节必须和明文镜像逐字节相同，跨块 / 块内偏移的区间读也一样；头坏了、被截短了当「没有镜像」。
    static class RomFsImageGzTest
    {
        [SoloTest("--romfs-image-gz")]
        public static int Run()
        {
            string root = Path.Combine(Path.GetTempPath(), $"s2a_romfs_gz_{Environment.ProcessId}");
            string program = Path.Combine(root, "game"), rom = Path.Combine(program, "romfs");
            int failures = 0;
            void Check(string name, string expected, string actual)
            {
                bool ok = expected == actual;
                if (!ok) failures++;
                Console.WriteLine($"{name}：期望 {expected} → 实 {actual} {(ok ? "✓" : "✗")}");
            }
            static byte[] All(IStorage s)
            {
                s.GetSize(out long n).ThrowIfFailure();
                byte[] b = new byte[n];
                s.Read(0, b).ThrowIfFailure();
                return b;
            }
            try
            {
                // 1. 造一份目录布局的 RomFS，写出索引；期望值取目录布局（不跑镜像写入方）
                Directory.CreateDirectory(Path.Combine(rom, "Data"));
                Dictionary<string, byte> files = new() { ["a.bin"] = 0x11, ["Data/b.bin"] = 0x22, ["Data/c.bin"] = 0x33 };
                foreach ((string rel, byte v) in files)
                {
                    byte[] data = new byte[1000 + v];
                    Array.Fill(data, v);
                    File.WriteAllBytes(Path.Combine(rom, rel), data);
                }
                string index = Path.Combine(program, RomFsIndex.FileName);
                RomFsIndex.Write(rom, index);
                byte[] want;
                using (IStorage byDir = RomFsIndex.Open(rom, index)) want = All(byDir);

                // 2. 按索引顺序拼明文镜像 + 对照表
                byte[] idx = File.ReadAllBytes(index);
                int nf = BitConverter.ToInt32(idx, 16), p = 20;
                int pathBase = 20 + nf * 24 + 4;
                long[] offsets = new long[nf];
                string plain = Path.Combine(program, RomFsIndex.ImageName), gzPath = plain + ".gz";
                using (FileStream image = File.Create(plain))
                {
                    for (int i = 0; i < nf; i++, p += 24)
                    {
                        string rel = Encoding.UTF8.GetString(idx, pathBase + BitConverter.ToInt32(idx, p + 16), BitConverter.ToInt32(idx, p + 20));
                        offsets[i] = image.Position;
                        image.Write(File.ReadAllBytes(Path.Combine(rom, rel)));
                    }
                }
                long imageLength = new FileInfo(plain).Length;
                using (BinaryWriter map = new(File.Create(Path.Combine(program, RomFsIndex.ImageMapName))))
                {
                    map.Write("S2ARIM01"u8);
                    map.Write(imageLength);
                    map.Write(nf);
                    foreach (long o in offsets) map.Write(o);
                }

                // 3. 明文先走一遍（对照基线），再换成压缩镜像
                using (IStorage byPlain = RomFsIndex.OpenImage(program))
                    Check("明文镜像整份读 == 目录布局", "True", (byPlain != null && want.AsSpan().SequenceEqual(All(byPlain))).ToString());
                WriteGz(plain, gzPath, 1024);   // 1 KiB 一块：故意让文件跨块，块内偏移也走到
                File.Delete(plain);
                Check("只有 romfs.img.gz：算有镜像", "True", RomFsIndex.ImageExists(program).ToString());
                using (IStorage byGz = RomFsIndex.OpenImage(program))
                    Check("压缩镜像整份读 == 目录布局", "True",
                        (byGz != null && want.AsSpan().SequenceEqual(All(byGz))).ToString());
                using (IStorage byGz = RomFsIndex.OpenImage(program))
                {
                    Random rnd = new(20261112);
                    bool same = byGz != null;
                    for (int i = 0; same && i < 64; i++)
                    {
                        int at = rnd.Next(0, want.Length), len = Math.Min(rnd.Next(1, 4000), want.Length - at);
                        byte[] got = new byte[len];
                        same = byGz.Read(at, got).IsSuccess() && got.AsSpan().SequenceEqual(want.AsSpan(at, len));
                    }
                    Check("压缩镜像随机区间读 64 次 == 明文", "True", same.ToString());
                }

                // 4. 坏形态：索引表被截 → 头里记的逻辑长度与对照表对不上；魔数不对 → 都不当镜像用，也不炸
                using (FileStream cut = File.OpenWrite(gzPath)) cut.SetLength(new FileInfo(gzPath).Length - 8);
                Check("压缩镜像被截短（推到一半）不使用", "null", RomFsIndex.OpenImage(program) == null ? "null" : "已打开");
                File.WriteAllBytes(gzPath, new byte[] { 1, 2, 3 });
                Check("坏的压缩镜像不用、也不炸", "null", RomFsIndex.OpenImage(program) == null ? "null" : "已打开");
                File.Delete(gzPath);
                Check("压缩镜像删掉后照旧报缺", "null", RomFsIndex.OpenImage(program) == null ? "null" : "已打开");
            }
            finally
            {
                try { Directory.Delete(root, true); }
                catch (IOException error)   // rom/ 下的句柄不一定当场收回（既有缓存行为）：清理失败只记账，别把断言结果盖掉
                {
                    Console.Error.WriteLine($"[romfs-image-gz] 临时目录删不掉：{error.Message}");
                }
            }
            return failures == 0 ? 0 : 1;
        }

        /// <summary>按 pack/phone_pack/gzimage.py 的格式写一份分块 gzip 镜像（各字段小端）。</summary>
        private static void WriteGz(string src, string dst, int block)
        {
            byte[] all = File.ReadAllBytes(src);
            int count = (all.Length + block - 1) / block;
            using FileStream f = File.Create(dst);
            f.Write("S2AGZ01\0"u8);
            f.Write(BitConverter.GetBytes((long)all.Length));
            f.Write(BitConverter.GetBytes(block));
            f.Write(BitConverter.GetBytes(count));
            f.Write(BitConverter.GetBytes(0L));
            List<(long Offset, int Packed, int Raw)> rows = new(count);
            for (int i = 0; i < count; i++)
            {
                int at = i * block, n = Math.Min(block, all.Length - at);
                using MemoryStream ms = new();
                using (GZipStream gz = new(ms, CompressionLevel.Optimal, true)) gz.Write(all, at, n);
                rows.Add((f.Position, (int)ms.Length, n));
                ms.Position = 0;
                ms.CopyTo(f);
            }
            long indexOffset = f.Position;
            foreach ((long offset, int packed, int raw) in rows)
            {
                f.Write(BitConverter.GetBytes(offset));
                f.Write(BitConverter.GetBytes(packed));
                f.Write(BitConverter.GetBytes(raw));
            }
            f.Position = 24;
            f.Write(BitConverter.GetBytes(indexOffset));
        }
    }
}
