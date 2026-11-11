#!/usr/bin/env python3
"""重组发布包（本支线：出包机只有 .NET SDK，没有 clang / clang++ / cmake / ninja / cargo）。

  python -X utf8 launchers/windows/repack_win.py <目标> --from <现有产品目录> [--version X.Y.Z]

和 bundle_win.py 的差别只有预编件这一段：
  ① C/C++ / Rust / 官方件（spvc、dsx、Yaz0、CRI、rewriter、glslang、devpush、rcodesign）从 <现有>/Engine/Binaries/Prepare 整份沿用
     —— 本机编不出，所以逐个核身份：有摘要口径的件必须与按本仓库源码算出的身份一致，不一致当场红；没有摘要口径的件（devpush 等）只能照搬，会单独提示；
  ② 4 件 C# 件（romfs / dsh_batch / nx_unpack / gamedec）用 .NET SDK 重编并重写 .src_digest / .xfp（同一个函数：bundle_prepare_win.dotnet_tools）。
其余与 bundle_win 一致：引擎快照（按 launchers/shared/engine_snapshot.py 的 INPUTS，全新单提交 Git 库）、Runtime / Shell / Fonts / ThirdParty/Impactor
核对后收入（Impactor 逐个核 SHA-256）、engine.json、version.txt、包根入口。

产物（<目标> 不许已存在）：
  Engine/             引擎快照 + Binaries/Prepare（沿用 + 重编的 4 件 + Tzdata）+ engine.json
  Runtime/ Shell/ Fonts/ ThirdParty/Impactor/
  version.txt  s2armctl.py  s2armctl.cmd  修复环境.cmd  skill/s2arm-operator/SKILL.md
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "launchers/windows"))
sys.path.insert(0, str(ROOT / "launchers/macos"))
sys.path.insert(0, str(ROOT / "launchers/shared"))
try:
    import bundle_engine            # 完整树（Mac 那份）：文件挑选 / 源码指纹 / engine.json 写法只此一份
except ImportError:                 # 本支线树没有 Mac 发版脚本：同一口径落在 launchers/shared/engine_snapshot.py
    import engine_snapshot as bundle_engine
import bundle_win                   # 复用 snapshot()：INPUTS → 全新单提交 Git 库
import bundle_prepare_win as prep
sys.path.insert(0, str(ROOT / "pack/jobs"))
import impactor

STAGE = ROOT / "build/windows/repack"


def say(m):
    print(m, flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pick(*cands):
    """按顺序找第一个存在的文件：本支线把 s2armctl.py / skill 放在仓库根（Mac 发版树里在 launchers/macos 下）"""
    for c in cands:
        if Path(c).is_file():
            return Path(c)
    raise SystemExit("【红】找不到：" + "、".join(str(c) for c in cands))


def check_source(src):
    """现有产品目录里必须有这几样，缺一个就先说清楚，别打到一半才红"""
    for rel in ("Engine/Binaries/Prepare", "Engine/Binaries/Prepare/Tzdata", "Runtime/s2arm_env.cmd",
                "Shell/manifest.json", "Fonts", "ThirdParty/Impactor"):
        if not (src / rel).exists():
            raise SystemExit(f"【红】{src} 里没有 {rel}：要指一份装好的产品目录（不是仓库）")
    bft = sorted((src / "Fonts").glob("*.bfttf"))
    if len(bft) != 7:
        raise SystemExit(f"【红】{src}/Fonts 里有 {len(bft)} 份 .bfttf，要 7 份")


def reuse_prepare(engine, src):
    """沿用现有预编件（C/C++ / Rust / 官方件），再重编 4 件 C# 的"""
    tools = engine / "Binaries/Prepare"
    shutil.copytree(src / "Engine/Binaries/Prepare", tools)
    say("[预编件] 沿用现有件：" + "、".join(sorted(p.name for p in tools.iterdir() if p.is_dir())))
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    say("[预编件] 用 .NET SDK 重编 4 件 C#（romfs / dsh_batch / nx_unpack / gamedec）")
    prep.dotnet_tools(tools, STAGE)
    prep.write_manifest(tools)                     # prepare.json 按重编后的实际文件重写
    shutil.rmtree(STAGE, ignore_errors=True)
    # 身份核对：有摘要口径的件必须与本仓库源码算出的身份一致（不一致 = 这件是别的源码编的，装上去会静默错）
    sys.path.insert(0, str(ROOT / "tools"))
    import env_repair
    rows = env_repair.check(tools)
    bad = [r for r in rows if r["state"] != "ok"]
    for r in rows:
        say(f"  {r['state']:8} {r['name']}：{r['why']}")
    if bad:
        raise SystemExit("【红】上面这些预编件按本仓库源码核不过（本机没有 C/C++ / Rust 工具链，编不出来）："
                         "在有 clang / cargo 的出包机上重跑 launchers/windows/bundle_prepare_win.py，或换一份同源的现有产品目录")
    return tools


def copy_impactor(target, src, say):
    """官方 Impactor / plumesign 原样随包：逐个核 impactor.BUNDLED 的 SHA-256，不符拒打"""
    dest = target / "ThirdParty/Impactor"
    shutil.copytree(src / "ThirdParty/Impactor", dest)
    for name, want in impactor.BUNDLED.items():
        p = dest / name
        if not p.is_file():
            raise SystemExit(f"【红】现有产品目录里缺 Impactor 件 {name}（应在 {src}/ThirdParty/Impactor）")
        got = sha256(p)
        if got != want:
            raise SystemExit(f"【红】{name} 的 SHA-256 是 {got}，钉的是 {want}：不是官方 v{impactor.TESTED} 原版")
    say(f"[组装] Impactor v{impactor.TESTED} 核对通过（{len(impactor.BUNDLED)} 个文件）")


def bundle(target, src, version=None):
    target, src = Path(target).resolve(), Path(src).resolve()
    if target.exists():
        raise SystemExit(f"【红】{target} 已存在：换个空目标，不混旧件")
    if target == src or src in target.parents:
        raise SystemExit("【红】目标不能放在现有产品目录里面")
    check_source(src)
    sys.path.insert(0, str(ROOT))
    from pack.shell import manifest as shell_manifest
    want = shell_manifest.current_id()
    have = json.loads((src / "Shell/manifest.json").read_text(encoding="utf-8"))["details"]["shellId"]
    if have != want:
        raise SystemExit(f"【红】现有产品目录的壳是 {have}，本引擎要 {want}：壳要在 Mac 上按同一提交重出（pack/shell_build.sh）")
    version = bundle_win.resolve_version(version)

    engine = target / "Engine"
    say(f"[组装] 引擎快照：{bundle_win.snapshot(engine)} 个文件")
    reuse_prepare(engine, src)
    say("[组装] 随包运行时"); shutil.copytree(src / "Runtime", target / "Runtime")
    shutil.copytree(src / "Shell", target / "Shell")
    say("[组装] 字体"); shutil.copytree(src / "Fonts", target / "Fonts")
    copy_impactor(target, src, say)

    fingerprint = bundle_engine.source_fingerprint()
    identity = bundle_engine.write_manifest(engine, fingerprint, version=version, shell_id=want)
    say(f"[组装] engine.json：{identity[:16]}（版本 {version}，壳 {want}）")
    (target / "version.txt").write_text(version + "\n", encoding="ascii")
    shutil.copy2(pick(ROOT / "launchers/macos/s2armctl.py", ROOT.parent / "s2armctl.py"), target / "s2armctl.py")
    (target / "s2armctl.cmd").write_text('@"%~dp0Runtime\\python\\python.exe" -X utf8 -B -I "%~dp0s2armctl.py" %*\r\n',
                                         encoding="ascii", newline="")
    (target / "修复环境.cmd").write_text('@echo off\r\nchcp 65001 >nul\r\n"%~dp0Runtime\\python\\python.exe" -X utf8 -B -I '
                                         '"%~dp0Engine\\tools\\env_repair.py" %*\r\nif "%~1"=="" pause\r\n',
                                         encoding="ascii", newline="")
    (target / "skill/s2arm-operator").mkdir(parents=True)
    shutil.copy2(pick(ROOT / "launchers/macos/skill/s2arm-operator/SKILL.md", ROOT.parent / "skill/s2arm-operator/SKILL.md"),
                 target / "skill/s2arm-operator/SKILL.md")
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    say(f"🟢 发布包 → {target}（{size / 2**30:.2f} GB）")
    say("   下一步自检：修复环境.cmd --deep（或在引擎目录跑 Runtime\\python\\python.exe Engine\\tools\\env_repair.py --deep）")
    return identity


def main(argv=None):
    ap = argparse.ArgumentParser(description="重组发布包（沿用现有 C/C++ 预编件 + 重编 C# 件）")
    ap.add_argument("target")
    ap.add_argument("--from", dest="source", required=True, help="现有产品目录（要含 Engine/Binaries/Prepare、Runtime、Shell、Fonts、ThirdParty/Impactor）")
    ap.add_argument("--version", help="版本号 X.Y.Z（缺省取最近的 tag，再缺省取仓库根 version.txt）")
    a = ap.parse_args(argv)
    bundle(a.target, a.source, a.version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
