# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 S2Arm contributors
"""构建独立 IPA（standaloneIpa 作业；装机页「构建独立IPA」按钮、出包页勾选「同时构建独立 IPA」）。

一款一个 App：容器里只放这一款，包名 io.s2arm.games.<游戏号>，显示名和图标用这款游戏自己的（玩家改过名字/封面就用改后的）。
产物放回这款的 image/ios/<游戏号>.ipa，由库一起管理；装机页旁边的「导出…」另存到玩家选的位置。
只出文件、不装机（玩家用 Impactor / AltStore 侧载）：每个独立 App 占一个免费签名名额（每台最多 3 个），
和共用容器（io.s2arm.games，一个 App 装多款）是两个东西，手机上可以并存。

请求：{gameId}；结果：{outputPath, bytes, bundleId}
"""
from __future__ import annotations
import shutil
from pathlib import Path

import ipa as ipa_jobs
import layout
from core import Failure


def output(c, gid):
    """独立 IPA 在库里的位置：这款的 image/ios/<游戏号>.ipa"""
    return layout.image_ios(c, gid) / (gid.upper() + ".ipa")


def build(c, gid):
    """出这一款的独立 IPA → {outputPath, bytes, bundleId}；没出过 iPhone 版 / 不是本壳出的，直接说清楚"""
    gid = gid.upper()
    sid = ipa_jobs.current_shell_id(c)
    if ipa_jobs.usable_kit(c, gid, sid) is None:
        raise Failure("这款的 iPhone 版不是这一版 S2Arm 出的（或还没出）：先「打 iPhone 包」，再构建独立 IPA。", code="container_stale")
    made, games, _ = ipa_jobs.assemble(c, gid, (), standalone=True)
    if gid not in games:
        raise Failure("组装出来的独立包不是这款（产物根程序号和游戏库不一致），没有保存。", code="job_failed")
    dest = output(c, gid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 游戏库可能不在应用所在的盘（11-11 实测：临时目录在 E:、这款的游戏文件夹在 G:），os.replace 跨卷会 WinError 17。
    #   shutil.move 同卷走 rename、跨卷走「复制 + 删源」，两种情况都不留第二份
    made = Path(made)
    shutil.move(str(made), str(dest))
    if made.parent.name.startswith(".container-"):   # 组装用的临时目录（S2Arm.app + 刚搬走的 ipa）一并清掉
        shutil.rmtree(made.parent, ignore_errors=True)
    return dict(outputPath=str(dest), bytes=dest.stat().st_size, bundleId=ipa_jobs.standalone_bundle(gid))


def run(c):
    gid = str(c.r.get("gameId") or "").upper()
    if not gid or layout.find_game(c, gid) is None:
        raise Failure("游戏库里没有这款游戏。", code="job_failed")
    c.begin("构建独立 IPA", 1, 2)
    c.emit("message", "正在组装只含这一款、独立包名的 App…")
    made = build(c, gid)
    c.begin("构建独立 IPA", 2, 2)
    c.emit("message", "已生成 %s（包名 %s）" % (made["outputPath"], made["bundleId"]))
    return made
