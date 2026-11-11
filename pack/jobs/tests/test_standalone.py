# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 S2Arm contributors
"""构建独立 IPA（pack/jobs/standalone.py，装机页「构建独立IPA」/ 出包页勾选）。

真出包要整套 iOS 壳和容器件（几分钟），这里只钉两件事：包名口径（io.s2arm.games.<游戏号小写>）、
以及没出过 iPhone 版 / 库里没这款时不凭空造包、把原因说清楚。
"""
import json

import jobcase   # 先导入：它把 pack/jobs 挂到 sys.path，ipa / layout / standalone 才导得进来
import ipa
import layout
import standalone


class StandaloneIpaTest(jobcase.JobCase):
    gid = "0100000000010000"

    def game(self, name="合成游戏"):
        folder = layout.game_dir(self.c, self.gid, english="Synthetic")
        (folder / layout.GAME_JSON).write_text(
            json.dumps(dict(schema=1, id=self.gid, name=name, ios=dict(shellId="SID1", pack="cafe01")), ensure_ascii=False),
            encoding="utf-8")
        return folder

    def test_bundle_id_is_per_game(self):
        self.assertEqual(ipa.standalone_bundle(self.gid), "io.s2arm.games.0100000000010000")
        self.assertNotEqual(ipa.standalone_bundle(self.gid), ipa.CONTAINER_BUNDLE, "独立包不能和共用容器同包名")

    def test_output_sits_next_to_the_iphone_image(self):
        self.game()
        self.assertEqual(standalone.output(self.c, self.gid), layout.image_ios(self.c, self.gid) / (self.gid + ".ipa"))

    def test_game_without_iphone_pack_is_refused(self):
        self.game("还没出包")
        result, terminal = self.worker("standaloneIpa", gameId=self.gid)
        self.assertEqual(terminal["state"], "failed")
        self.assertIn("打 iPhone 包", terminal["message"])
        self.assertFalse(standalone.output(self.c, self.gid).exists(), "没出成不能留半份包")

    def test_unknown_game_is_refused(self):
        result, terminal = self.worker("standaloneIpa", gameId="0100000000099999")
        self.assertEqual(terminal["state"], "failed")
        self.assertIn("没有这款游戏", terminal["message"])
