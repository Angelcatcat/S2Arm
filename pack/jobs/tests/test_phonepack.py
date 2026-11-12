# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 S2Arm contributors
"""生成手机资源包（pack/jobs/phonepack.py，启动器装机页「生成手机资源包」按钮）。

期望按 make_phone_pack.py 的口径手写：产物 <输出目录>/<游戏号>/资源包，里面 romfs.img 是单文件镜像、着色器逐文件，
index.tsv / manifest.json 一起带过去；还没出过 iPhone 版的游戏不凭空造包、直接说「先打 iPhone 包」。
"""
import json
import os
import sys
from pathlib import Path

import jobcase
import layout

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "phone_pack"))   # 产物是分块 gzip 镜像，按格式解开核内容
import gzimage  # noqa: E402


class PhonePackTest(jobcase.JobCase):
    gid = "0100000000010000"

    def game(self, name="合成游戏"):
        """这款的文件夹与 game.json（显式 utf-8：作业进程按 utf-8 读，不跟本机代码页）"""
        folder = layout.game_dir(self.c, self.gid, english="Synthetic")
        (folder / layout.GAME_JSON).write_text(
            json.dumps(dict(schema=1, id=self.gid, name=name, ios=dict(shellId="SID1", pack="cafe01")), ensure_ascii=False),
            encoding="utf-8")
        return folder

    def pack_fixture(self):
        """库里的 iPhone 资源包：镜像已拼好（romfs.img 一个文件）+ 逐文件着色器"""
        self.game()
        pack = layout.image_ios(self.c, self.gid) / "pack"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "romfs.img").write_bytes(b"IMG!!")
        (pack / "shaders").mkdir(exist_ok=True)
        (pack / "shaders" / "a.bin").write_bytes(b"abc")
        (pack / "index.tsv").write_bytes(b"romfs.img\t5\th\nshaders/a.bin\t3\tq\n")
        (pack / "manifest.json").write_bytes(b'{"version": 2}')
        return pack

    def test_builds_pack_next_to_private_root(self):
        self.pack_fixture()
        result, terminal = self.worker("phonePack", gameId=self.gid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(terminal["state"], "succeeded")
        out = Path(terminal["result"]["outputPath"])
        self.assertEqual(out, self.c.private / "phone_pack" / self.gid)
        self.assertEqual((out / "资源包" / "romfs.img").read_bytes(), b"IMG!!")
        self.assertEqual((out / "资源包" / "shaders" / "a.bin").read_bytes(), b"abc")
        self.assertTrue((out / "资源包" / "index.tsv").is_file())
        self.assertEqual(json.loads((out / "资源包" / "manifest.json").read_text()), {"version": 2})

    def test_location_from_request_is_used(self):
        self.pack_fixture()
        target = self.root / "手机" / "games"
        result, terminal = self.worker("phonePack", gameId=self.gid, location=str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(terminal["result"]["outputPath"]), target / self.gid)
        self.assertTrue((target / self.gid / "资源包" / "romfs.img").is_file())

    def test_game_without_iphone_pack_is_refused(self):
        self.game("还没出包")
        result, terminal = self.worker("phonePack", gameId=self.gid)
        self.assertEqual(terminal["state"], "failed")
        self.assertIn("打 iPhone 包", terminal["message"])
        self.assertFalse((self.c.private / "phone_pack" / self.gid).exists())

    def test_unknown_game_is_refused(self):
        result, terminal = self.worker("phonePack", gameId="0100000000099999")
        self.assertEqual(terminal["state"], "failed")
        self.assertIn("没有这款游戏", terminal["message"])

    # ---- 检测压缩收益（phonePackEstimate）：装机页「检测压缩收益」按钮 + 生成前的「是否压缩」确认框 ----

    def estimate_fixture(self, compressible=True):
        """用来估收益的资源包：可压的（全零）或压不动的（随机），外加一份索引"""
        self.game()
        pack = layout.image_ios(self.c, self.gid) / "pack"
        pack.mkdir(parents=True, exist_ok=True)
        blob = b"\0" * (64 << 10) if compressible else os.urandom(64 << 10)
        (pack / "data.bin").write_bytes(blob)
        (pack / "index.tsv").write_bytes(b"data.bin\t%d\tx\n" % len(blob))
        return pack

    def test_estimate_reports_gain(self):
        pack = self.estimate_fixture()
        result, terminal = self.worker("phonePackEstimate", gameId=self.gid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(terminal["state"], "succeeded")
        est = terminal["result"]
        self.assertEqual(est["rawBytes"], sum(p.stat().st_size for p in pack.rglob("*") if p.is_file()))
        self.assertEqual(est["savedBytes"], est["rawBytes"] - est["packedBytes"])
        self.assertGreater(est["packedBytes"], 0)
        self.assertLessEqual(est["packedBytes"], est["rawBytes"])   # 压不动也不报「比原来还大」
        self.assertGreater(est["sampledBytes"], 0)
        self.assertTrue(est["worthIt"])
        self.assertEqual(est["verdict"], "worth")
        self.assertEqual(est["byKind"][0]["ext"], ".bin")   # 按体积降序，最大的一类在最前
        self.assertEqual(est["byKind"][0]["files"], 1)

    def test_estimate_thin_when_nothing_compresses(self):
        self.estimate_fixture(compressible=False)
        result, terminal = self.worker("phonePackEstimate", gameId=self.gid)
        self.assertEqual(result.returncode, 0, result.stderr)
        est = terminal["result"]
        self.assertEqual(est["verdict"], "thin")
        self.assertFalse(est["worthIt"])

    def test_estimate_is_read_only(self):
        pack = self.estimate_fixture()
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                  for p in pack.rglob("*") if p.is_file()}
        result, _ = self.worker("phonePackEstimate", gameId=self.gid)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
                 for p in pack.rglob("*") if p.is_file()}
        self.assertEqual(before, after)   # 只读：一个字节、一个 mtime 都不许动

    def test_estimate_refuses_without_iphone_pack(self):
        self.game("还没出包")
        result, terminal = self.worker("phonePackEstimate", gameId=self.gid)
        self.assertEqual(terminal["state"], "failed")
        self.assertIn("打 iPhone 包", terminal["message"])

    def test_compress_writes_block_gzip_image(self):
        """弹窗选「压缩」：romfs.img 写成 romfs.img.gz（分块 gzip），不落明文；解出来要与原镜像逐字节相同"""
        pack = self.pack_fixture()
        result, terminal = self.worker("phonePack", gameId=self.gid, compress=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(terminal["result"]["compressRequested"])
        out = Path(terminal["result"]["outputPath"]) / "资源包"
        self.assertFalse((out / "romfs.img").exists())          # 压缩形态不留明文
        gz = out / "romfs.img.gz"
        self.assertTrue(gz.is_file())
        self.assertEqual(gzimage.read_at(str(gz), 0, 5), b"IMG!!")
        self.assertEqual(gzimage.verify(str(gz))[0], 5)         # 容器头记的逻辑长度 == 原镜像长度
        # index.tsv 原样带过去：里面记的仍是 romfs.img 的逻辑长度与 sha256（手机端按逻辑长度核，不看磁盘大小）
        self.assertEqual((out / "index.tsv").read_bytes(), (pack / "index.tsv").read_bytes())
        self.assertEqual((out / "shaders" / "a.bin").read_bytes(), b"abc")   # 其余文件照旧是明文硬链接

    def test_compress_defaults_to_false(self):
        self.pack_fixture()
        result, terminal = self.worker("phonePack", gameId=self.gid)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(terminal["result"]["compressRequested"])
        out = Path(terminal["result"]["outputPath"])
        self.assertEqual((out / "资源包" / "romfs.img").read_bytes(), b"IMG!!")
        self.assertFalse((out / "资源包" / "romfs.img.gz").exists())
