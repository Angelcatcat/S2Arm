# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 S2Arm contributors
"""生成手机资源包（pack/jobs/phonepack.py，启动器装机页「生成手机资源包」按钮）。

期望按 make_phone_pack.py 的口径手写：产物 <输出目录>/<游戏号>/资源包，里面 romfs.img 是单文件镜像、着色器逐文件，
index.tsv / manifest.json 一起带过去；还没出过 iPhone 版的游戏不凭空造包、直接说「先打 iPhone 包」。
"""
import json
from pathlib import Path

import jobcase
import layout


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
