#!/usr/bin/env python3
"""环境修复工具（随包，Windows / Mac）：把启动器里的「缺少组件」查清楚，能安全修的当场修。

  python -X utf8 tools/env_repair.py                      诊断：引擎快照有没有被改动 + 逐件核预编件（在不在 / 身份对不对 / 源码在不在）
  python -X utf8 tools/env_repair.py --json               同上，机器读
  python -X utf8 tools/env_repair.py --deep               引擎快照连逐文件 sha256 一起核（几十秒）
  python -X utf8 tools/env_repair.py --fix                安全修复：清掉与当前身份不符的现编缓存（<引擎>/build 下）
  python -X utf8 tools/env_repair.py --from <引擎目录> --fix
                                                          再从同一份源码的引擎目录补预编件：逐件按身份核过才拷，身份不符一律不动
  python -X utf8 tools/env_repair.py --fix --fix-names    把被误转成 GBK/CP437 乱码名的中文文件名改回清单里的正确名字（只改名、不动内容）
  python -X utf8 tools/env_repair.py --tools <Prepare 目录>   默认取 S2A_PREBUILT_TOOLS，没有就取 <引擎>/Binaries/Prepare
退出码：0 全好（或用 --fix 修好了能修的）；1 还有修不了的；2 用法错

为什么要单独有这一件：使用方的 prebuilt()（translator/spvc_inproc.py:62、translator/dsh_batch.py:46、pack/orch/oneclick_win.py:172）
把「预编件文件不在 / .src_digest 与源码不符 / 源码缺算不出身份」三种情况都吞成 None，界面上都只剩一句「缺少工具」。
这里把三种分开报，并且判据与使用方同一份身份函数（不另写一套，避免两边算出不同的号）。
本工具只读源码、只动 <引擎>/build 与 Binaries/Prepare 里的预编件，不改任何 .py / .cs。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
for _p in (str(R), str(R / "translator"), str(R / "pack/jobs"), str(R / "graphics/widen/scan"),
           str(R / "graphics/tools"), str(R / "graphics/tools/shbake")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STAMP = ".src_digest"
WIN = os.name == "nt"
HEX16 = re.compile(r"\A[0-9a-f]{16}\Z")


def tools_dir(given=None):
    if given:
        return Path(given)
    d = os.environ.get("S2A_PREBUILT_TOOLS")
    return Path(d) if d else R / "Binaries/Prepare"


def _item(name, rels, ident_fn=None, stamp=None, note=""):
    """一件预编件：要核的文件（相对 Prepare）、身份函数、摘要文件。身份算不出来单独记（= 源码不全，使用方也会当缺件）
    摘要文件缺省是件目录下的 .src_digest（dsh_batch / nx_unpack / gamedec / spvc 的口径）；改写器是 <exe>.source，另给"""
    ident, err = None, ""
    if ident_fn is not None:
        try:
            ident = ident_fn()
        except Exception as e:                      # 源码缺 / 工程文件缺：报清楚，别混成「预编件没打包」
            err = f"{type(e).__name__}: {e}".strip()
    if ident_fn is not None and stamp is None:
        stamp = str(Path(rels[0]).parent / STAMP).replace("\\", "/")
    return {"name": name, "rels": list(rels), "ident": ident, "ident_error": err, "stamp": stamp, "note": note}


def collect():
    """与使用方逐个对齐：身份函数直接调它们自己的（见模块注释）。导入不到 = 这件在这棵树上本来就不存在，跳过"""
    out = []

    def add(fn):
        try:
            out.append(fn())
        except Exception as e:                      # 这件在这棵树上本来就不存在（引擎被剔过源码）：单独记，不挡别的件
            out.append({"name": f"{fn.__name__}（检查器起不来）", "rels": [], "ident": None,
                        "ident_error": f"{type(e).__name__}: {e}", "stamp": None, "note": ""})

    def romfs():
        return _item("romfs 索引", ["romfs/romfs_index.dll"], note="无摘要口径（使用方只核文件在不在）")

    def dsh():
        import dsh_batch
        csproj = os.path.join(dsh_batch.DSH, "batch", "dsh_batch.csproj")
        return _item("直连出库批处理 dsh_batch", ["dsh_batch/dsh_batch.dll", "dsh_batch/.xfp"],
                     lambda: dsh_batch.src_digest(csproj), note=".xfp = 发版按同一份源码写的翻译器指纹")

    def nx():
        from pack.orch import oneclick_win
        return _item("解包 nx_unpack", ["nx_unpack/nx_unpack.exe"], oneclick_win.nx_unpack_id)

    def gd():
        from pack.orch import oneclick_win
        return _item("游戏解码器 gamedec", ["gamedec/gamedec.exe"], oneclick_win.gamedec_id)

    def spvc():
        import spvc_inproc
        return _item("spirv-cross（进程内库 + 命令行）",
                     [f"spvc/{spvc_inproc.LIB}", f"spvc/cli/bin/{spvc_inproc.CLI}", "spvc/cli/versions.json"],
                     spvc_inproc.prebuilt_id, note="身份 = 输出身份 + Windows 胶水 win/shim.cpp")

    def dsx():
        return _item("直连翻译库 dsx", ["dsx/dsx.dll"], note="无摘要口径（使用方只核文件在不在）")

    def yaz0():
        import ui_lyt
        return _item("Yaz0 编码器", [f"codecs/{ui_lyt.yaz0enc_name()}"], note="名字即源码身份（yaz0enc.c 的 SHA-1 前 12 位）")

    def yaz0dec():
        import nxfmt
        return _item("Yaz0 解码库", [f"codecs/{nxfmt._lib_name()}"], note="名字即源码身份")

    def cri():
        import nxsh_extract
        return _item("CRI 解压小库", [f"codecs/{nxsh_extract.cri_lib_name()}"], note="名字即源码身份")

    def rewriter():
        from pack import rewriter as rw
        name = "s2a-rewrite.exe" if WIN else "s2a-rewrite"
        return _item("C 改写器", [f"rewriter/{name}", f"rewriter/{name}.source"],
                     rw.source_digest, stamp=f"rewriter/{name}.source", note="身份写在 <exe>.source")

    for fn in (romfs, dsh, nx, gd, spvc, dsx, yaz0, yaz0dec, cri, rewriter):
        add(fn)
    for name, rel in (("推资源包 devpush", "devpush/s2a-devpush.exe"), ("证书签名 rcodesign", "rcodesign/rcodesign.exe"),
                      ("OpenGL 翻译 glslang", "glslang/glslangValidator.exe")):
        out.append(_item(name, [rel], note="官方 / 外部预编件，无摘要口径"))
    return out


def status(tools, item):
    """ok / missing（文件不在）/ mismatch（摘要不符）/ broken（源码缺、身份算不出）"""
    if item["ident_error"]:
        return "broken", item["ident_error"]
    missing = [r for r in item["rels"] if not (tools / r).is_file()]
    if item["ident"] and item["stamp"]:
        try:
            got = (tools / item["stamp"]).read_text(encoding="utf-8").strip()
        except OSError:
            if missing:
                return "missing", "缺 " + "、".join(missing)
            return "missing", f"缺 {item['stamp']}（被删 / 拷贝时漏了隐藏文件）"
        if got != item["ident"]:
            return "mismatch", f"{item['stamp']} = {got}，按本引擎源码算出 {item['ident']}（预编件不是按这份源码编的）"
    if missing:
        return "missing", "缺 " + "、".join(missing)
    return "ok", item["note"] or "齐"


def check(tools):
    rows = []
    for item in collect():
        state, why = status(tools, item)
        rows.append(dict(item, state=state, why=why))
    return rows


def clean_cache(applied):
    """清与当前身份不符的现编缓存：只删 <引擎>/build 下按旧身份编出来的目录，当前身份那份不动
    （只能清「有编译器才可能产生」的缓存；玩家机上没 clang++，_build_id() 问不到编译器版本，本来也不会有缓存，直接跳过）"""
    root = R / "build" / "spvc_cli"
    if not root.is_dir():
        return
    try:
        import spvc_inproc
        cur = spvc_inproc._build_id()
    except (ImportError, OSError, ValueError):
        return
    for d in root.iterdir():
        if d.is_dir() and d.name != cur:
            shutil.rmtree(d, ignore_errors=True)
            applied.append(f"清掉旧的 spirv-cross 现编缓存 build/spvc_cli/{d.name}（当前身份 {cur}）")


def same_build(source):
    """来源引擎与本引擎是不是同一次打包（engine.json 的 id）：是的话，没摘要口径的件（romfs / dsx / 小库 / 外部件）也能照搬"""
    try:
        ours = json.loads((R / "engine.json").read_text(encoding="utf-8")).get("id")
        them = json.loads((Path(source) / "engine.json").read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError):
        return False
    return bool(ours) and ours == them


def restore(tools, source, applied, blocked):
    """从另一份引擎目录补预编件：有摘要口径的件必须「来源身份 = 本引擎源码算出的身份」才拷；
    没摘要口径的件（romfs / dsx / Yaz0 / CRI / 外部件）只在两边是同一次打包（engine.json 的 id 相同）时才补"""
    src = Path(source) / "Binaries/Prepare"
    if not src.is_dir():
        raise SystemExit(f"【红】{src} 不是预编件目录（要给引擎目录，里面有 Binaries/Prepare）")
    whole = same_build(source)
    for item in collect():
        rels = list(item["rels"])
        if item["ident"] and not item["ident_error"]:
            try:
                them = (src / item["stamp"]).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if them != item["ident"]:
                blocked.append(f"{item['name']}：来源那份的身份是 {them}，与本引擎源码算出的 {item['ident']} 不同，不拷")
                continue
            rels = list(dict.fromkeys([*rels, item["stamp"]]))     # 摘要文件一起补，缺了它使用方也不认
        elif not whole:
            if any(not (tools / r).is_file() for r in rels):
                blocked.append(f"{item['name']}：这件没有摘要口径，只能整包同源才补（来源 engine.json 与本引擎不是同一次打包）")
            continue
        for rel in rels:
            s, d = src / rel, tools / rel
            if not s.is_file() or (d.is_file() and d.stat().st_size == s.stat().st_size):
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            applied.append(f"补 {rel}")


def snapshot(deep=False):
    """按 <引擎>/engine.json 核引擎快照本身：文件在不在、大小对不对（--deep 再逐个核 sha256）。
    「预编件与源码对不对得上」和「这份安装有没有被改动 / 掉文件」是两件事，处理办法完全不同，分开报。
    源码树（开发用）没有 engine.json → 不核（返回 None）"""
    eng = R / "engine.json"
    if not eng.is_file():
        return None
    try:
        m = json.loads(eng.read_text(encoding="utf-8"))
    except ValueError:
        return dict(id="", version="", state="broken", why="engine.json 不是合法 JSON，这份安装已损坏", bad=[])
    files = m.get("files") or {}
    bad, renames = [], []
    for rel, meta in files.items():
        name = rel.replace("\\", "/")
        if name.startswith(".git/"):
            continue                                  # 快照自带的 Git 元数据：正常使用就会变（同 embedded._stable_files）
        p = R / name
        if not p.is_file():
            alt = mojibake_name(name)                 # 中文名被 GBK/CP437 误转时，磁盘上是那个乱码名（本支线实测有这批文件）
            if alt and (R / alt).is_file():
                renames.append((name, alt))
                bad.append(f"{name}：磁盘上是乱码名 {alt}（--fix-names 可改回）")
            else:
                bad.append(f"缺 {name}")
            continue
        if not isinstance(meta, list) or not meta:
            continue
        if isinstance(meta[0], int) and p.stat().st_size != meta[0]:
            bad.append(f"{name}：大小 {p.stat().st_size} ≠ 清单 {meta[0]}")
        elif deep and len(meta) > 1 and isinstance(meta[1], str) and len(meta[1]) == 64:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            if h.hexdigest() != meta[1]:
                bad.append(f"{name}：内容摘要不符（文件被改过）")
    return dict(id=str(m.get("id", "")), version=str(m.get("version", "")), state="ok" if not bad else "broken",
                why=f"{len(files)} 个文件全部对得上" if not bad else f"{len(bad)} 个文件不对",
                bad=bad[:40], renames=renames)


def mojibake_name(name):
    """正确的中文名在 GBK / CP437 之间被误转一次后的样子（「设计.md」→「╔Φ╝╞.md」）。
    本支线的引擎树里有一批 .md / 来源.txt 就是这么变成乱码名的：内容没坏，名字对不上 engine.json 的清单"""
    try:
        return name.encode("gbk").decode("cp437")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def fix_names(renames, applied):
    """把乱码名改回清单里的正确名字（只改名字、不动内容；目标已存在的一律不动）"""
    for name, alt in renames:
        src, dst = R / alt, R / name
        if not src.is_file() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        applied.append(f"改回文件名 {alt} → {name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="环境修复：核引擎快照与预编件、清过期现编缓存、从同源引擎补件")
    ap.add_argument("--tools", help="预编件目录（默认 S2A_PREBUILT_TOOLS 或 <引擎>/Binaries/Prepare）")
    ap.add_argument("--from", dest="source", help="另一份引擎目录（补件用，必须有 Binaries/Prepare）")
    ap.add_argument("--fix", action="store_true", help="执行安全修复（清缓存；给了 --from 就补件；给了 --fix-names 就改回乱码文件名）")
    ap.add_argument("--fix-names", action="store_true", help="把被误转成 GBK/CP437 乱码名的中文文件名改回 engine.json 清单里的正确名字")
    ap.add_argument("--deep", action="store_true", help="引擎快照连 sha256 一起核（慢，约几十秒）")
    ap.add_argument("--json", action="store_true", help="机器读输出")
    a = ap.parse_args(argv)
    if (a.source or a.fix_names) and not a.fix:
        ap.error("--from / --fix-names 要配 --fix 一起用（本工具默认只读）")
    tools = tools_dir(a.tools)
    rows = check(tools)
    snap = snapshot(a.deep)
    applied, blocked = [], []
    if a.fix:
        clean_cache(applied)
        if a.fix_names and snap:
            fix_names(snap["renames"], applied)
        if a.source:
            restore(tools, a.source, applied, blocked)
        rows = check(tools)                    # 修完再核一遍，报的是修后的状态
        snap = snapshot(a.deep)
    bad = [r for r in rows if r["state"] != "ok"]
    bad_snap = bool(snap and snap["state"] != "ok")
    if a.json:
        print(json.dumps(dict(tools=str(tools), engine=str(R), snapshot=snap,
                              items=[dict(name=r["name"], state=r["state"], why=r["why"], ident=r["ident"], files=r["rels"]) for r in rows],
                              fixed=applied, blocked=blocked, ok=not bad and not bad_snap), ensure_ascii=False, indent=2))
    else:
        print(f"引擎 {R}")
        if snap:
            print(f"  版本 {snap['version'] or '?'}　引擎号 {snap['id'][:16] or '?'}")
            print(f"  {'✅' if not bad_snap else '⚠️ 安装被改动'}            引擎快照：{snap['why']}")
            for line in snap["bad"][:10]:
                print(f"       · {line}")
            if len(snap["bad"]) == 40:
                print("       · …（更多见 --json）")
        print(f"预编件 {tools}")
        for r in rows:
            mark = {"ok": "✅", "missing": "❌ 缺件", "mismatch": "⚠️ 身份不符", "broken": "⚠️ 源码不全"}[r["state"]]
            print(f"  {mark:12} {r['name']}：{r['why']}")
        if applied:
            print("\n已修：")
            for line in applied:
                print("  • " + line)
        if blocked:
            print("\n没动（要出包侧重出）：")
            for line in blocked:
                print("  • " + line)
        if bad_snap:
            print("\n结论：这份安装的引擎文件被改动或缺失（杀软隔离 / 手动替换 / 文件名被误转码都算）。")
            if snap["renames"]:
                print(f"  其中 {len(snap['renames'])} 个只是名字变成了 GBK/CP437 乱码，内容还在：--fix --fix-names 改回即可。")
            print("  其余缺件 / 大小不符不能就地手工补：换一份完整的安装目录，或重新解压完整包后只把 Engine 整份换掉。")
        if bad:
            print(f"\n结论：有 {len(bad)} 件预编件不可用，玩家机上没编译器，这几条链会停在「缺少工具」。")
            print("  能自己好的：文件被杀软隔离 / 拷贝漏了——用 --from <另一份同源引擎目录> --fix 补回来；")
            print("  摘要不符 = 预编件与这份源码不同源，只能在出包机上重跑 launchers/windows/bundle_prepare_win.py 重出整套。")
            print("  源码不全（⚠️）说明引擎快照打包时就少收了源码，同一份包在任何机器上都修不好，要换完整安装包。")
        if not bad and not bad_snap:
            print("\n结论：引擎快照完好、预编件齐、身份对得上。")
    return 0 if not bad and not bad_snap else 1


if __name__ == "__main__":
    sys.exit(main())
