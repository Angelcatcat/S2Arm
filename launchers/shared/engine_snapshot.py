"""引擎快照的挑选规则与 engine.json 清单（本支线树里没有 Mac 的 launchers/macos/bundle_engine.py，口径落在这里）。

bundle_win.py（全新出包）与 repack_win.py（没有 C/C++ 工具链的机器上重组）都只用这一份：
  INPUTS                收哪些仓库路径（顶层）
  include_source(rel)   逐个文件判：编译产物、编辑器 / 系统垃圾、出包期会变的，一律不收
  source_fingerprint()  引擎源码指纹（进产物根的键，同时写进 engine.json 的 source）
  write_manifest()      写 <引擎>/engine.json

engine.json 的消费方与硬要求：
  pack/jobs/embedded.py   schema 必须 1、id 必须 64 位小写十六进制；按 files 逐文件核大小后克隆进私有目录
  pack/jobs/host_update.py  files[rel] = [size, sha256]（取内置宿主的摘要前 12 位）
  pack/shell/manifest.py  发布快照读 shellId（16 位十六进制）当「本树该用的壳号」，不再按目录对象号重算
  pack/jobs/core.py       发布快照读 version（三端同号）
键一律是正斜杠的相对路径：pack/jobs/embedded.artifact 按 "Binaries/" + name 拼键，用过反斜杠的清单在 Windows 上取不到。
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INPUTS = ["compat", "graphics", "android", "runtime", "pack", "tools", "services", "translator",
          "launchers", "ios", "shell", "macos", "platform", "tests", "docs",
          "Makefile", "LICENSE", "README.md", ".gitignore"]

TOP_SKIP = {"Binaries", "build", ".git"}                      # 预编件由 bundle_prepare 出；清单自己；构建缓存
DIR_SKIP = {"__pycache__", ".pytest_cache", "node_modules", ".git"}
BUILD_DIRS = {"bin", "obj"}                                   # .NET 编译产物（只在有 .csproj 的目录下才整目录剔除）
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}
JUNK_SUFFIX = (".pyc", ".pyo", ".log", ".tmp", ".bak", ".orig", ".rej", ".swp")


def _dotnet_output(d):
    """这个 bin / obj 是不是 .NET 编译产物：同目录有工程文件才算（tools/bin/timeout 这种是源码，不能剔）"""
    try:
        return any(x.suffix in (".csproj", ".sln", ".fsproj") for x in d.parent.iterdir())
    except OSError:
        return False


def include_source(rel):
    parts = Path(rel).parts
    if not parts or parts[0] in TOP_SKIP:
        return False
    name = parts[-1]
    if name in JUNK or name.startswith("._") or name.endswith(JUNK_SUFFIX):
        return False
    for i, part in enumerate(parts[:-1]):
        if part in DIR_SKIP:
            return False
        if part in BUILD_DIRS and _dotnet_output(ROOT.joinpath(*parts[:i + 1])):
            return False
    return True


def sources():
    """仓库里该进快照的文件（相对路径，正斜杠）：按 INPUTS 走一遍，再逐个过 include_source"""
    out = []
    for top in INPUTS:
        base = ROOT / top
        if base.is_file():
            if include_source(top):
                out.append(top)
            continue
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and include_source(p.relative_to(ROOT)):
                out.append(p.relative_to(ROOT).as_posix())
    return sorted(out)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def source_fingerprint():
    """引擎源码指纹：相对路径 + 内容摘要，排序后汇总（改动一个字节就变，与提交号无关）"""
    h = hashlib.sha256()
    for rel in sources():
        h.update(rel.encode() + b"\0" + digest(ROOT / rel).encode() + b"\n")
    return h.hexdigest()


def write_manifest(engine, fingerprint=None, version="", shell_id="", memory_version=2):
    """写 <engine>/engine.json，返回 id（64 位）。files 用正斜杠相对路径 + [大小, sha256]
    必须在引擎目录里所有文件都就位之后调（含 Binaries/Prepare、Tzdata）"""
    engine = Path(engine)
    files = {}
    for p in sorted(engine.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(engine).as_posix()
        if rel == "engine.json":
            continue
        files[rel] = [p.stat().st_size, digest(p)]
    blob = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    identity = hashlib.sha256(blob).hexdigest()
    doc = dict(schema=1, id=identity, source=fingerprint or source_fingerprint(), version=version,
               memoryRequirementsVersion=memory_version, shellId=shell_id, files=files)
    (engine / "engine.json").write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return identity


def head():
    """仓库当前提交号（写进 prepare.json 的 source 用）"""
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def dirty():
    try:
        return bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                                   capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return False


if __name__ == "__main__":     # 自检：python -X utf8 launchers/shared/engine_snapshot.py
    s = sources()
    total = sum(os.path.getsize(ROOT / r) for r in s)
    print(f"{len(s)} 个文件，{total / 2**20:.1f} MB，源码指纹 {source_fingerprint()}")
    print("提交号 " + head() + ("（有未提交改动）" if dirty() else ""))
    sys.exit(0)
