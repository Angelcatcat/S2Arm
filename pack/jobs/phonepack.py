# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 S2Arm contributors
"""生成手机资源包（phonePack 作业；启动器装机页的「生成手机资源包」按钮）。

游戏库里的 image/ios/pack 是给装机用的形态：romfs.img 只存成 romfs.files/（逐文件硬链接），index.tsv 却记着
镜像本身的长度与 sha256。整个库直接拷进手机，镜像缺文件，手机端核对不过（库里的游戏块一直是灰色「安装中」）。
这里调引擎自带的 pack/phone_pack/make_phone_pack.py：现拼 romfs.img，其余文件（含着色器 shaders/，逐文件形态）
同卷硬链接（跨卷自动复制），拼完删临时镜像。

产物 <输出目录>/<游戏号>/资源包，整个「游戏号」文件夹拷进手机 App 的 Documents/games/ 下即可。
请求：{gameId, location?}；location 缺省 <私有目录>/phone_pack。结果：{outputPath, bytes}
"""
from __future__ import annotations
import sys
from pathlib import Path

import layout
from core import Failure


def phone_pack_core(engine):
    """引擎里的 pack/phone_pack/make_phone_pack.py（拼镜像的现成逻辑；没有就说清楚，不做第二份实现）"""
    d = Path(engine) / "pack" / "phone_pack"
    if not (d / "make_phone_pack.py").is_file():
        raise Failure("引擎里没有 pack/phone_pack/make_phone_pack.py，没法生成手机资源包。", code="engine_incomplete")
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import make_phone_pack
    return make_phone_pack


def run(c):
    gid = str(c.r.get("gameId") or "").upper()
    folder = layout.find_game(c, gid) if gid else None
    if folder is None:
        raise Failure("游戏库里没有这款游戏。", code="job_failed")
    pack = layout.ios_pack(c, gid)
    if not (pack / "index.tsv").is_file():
        # 还没出过 iPhone 版（image/ios/pack 是出包收尾才交付的）：先出包，别在这里凭空造一份
        raise Failure("这款还没有 iPhone 资源包：先点「打 iPhone 包」，再生成手机资源包。", code="job_failed")
    mod = phone_pack_core(c.engine)
    romfs = mod.load_engine(c.engine)
    rows = mod.read_index(str(pack))
    item = dict(name=layout.game_info(c, gid).get("name") or gid, gid=gid, pack=str(pack),
                bytes=sum(int(r[1]) for r in rows), ok=True, why="")
    total = item["bytes"] or 1
    out_root = Path(c.r.get("location") or (Path(c.private) / "phone_pack"))

    def emit(frac, text):
        c.check()   # 玩家点停：抛 Cancelled，build_one 的 finally 会删掉拼了一半的临时镜像
        c.emit("progress", message=text, done=int(frac * total), total=total, unit="B")

    c.begin("生成手机资源包", 1, 1)
    c.emit("stage", message="正在拼镜像、链接文件（%.2f GB）…" % (total / 1e9))
    written = mod.build_one(romfs, item, str(out_root), emit)
    out = out_root / gid
    c.emit("stage", message="已生成 %s" % out)
    return {"outputPath": str(out), "bytes": written}
