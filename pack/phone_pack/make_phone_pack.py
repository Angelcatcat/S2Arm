#!/usr/bin/env python3
"""把游戏库里的资源包整理成「拷进手机就能玩」的形态（核心逻辑 + 命令行入口）。

    python make_phone_pack.py [游戏库目录] [输出目录]

输出 <输出目录>/<游戏号>/资源包，就是手机上 Documents/games/<游戏号>/资源包。

库里按 #329 只存 romfs.files/（镜像内各文件的硬链接），index.tsv 里却记着 romfs.img 的长度与 sha256；直接
拷库到手机，镜像缺文件 → 手机端核对不过（游戏库里画成「安装中」的灰块）。这里用引擎自带的
pack/romfs_image.py 现拼 romfs.img，其余文件（含着色器 shaders/，逐文件形态）同卷硬链接（跨卷自动复制），
拼完删临时镜像。
"""
import json
import os
import shutil
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 分块 gzip 镜像（romfs.img.gz）：与本文件同目录，出包/自测都用它
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import gzimage          # <Engine>/pack/phone_pack（打包成 exe 后是临时解包目录）


def engine_ok(base):
    """目录里有没有 pack/romfs_image.py（判断它是不是引擎目录）。"""
    return bool(base) and Path(base, "pack", "romfs_image.py").is_file()


def find_engine(preferred=None):
    """引擎目录：界面上选的 / S2A_ENGINE 环境变量 > exe / 脚本旁边的 Engine > 脚本自己所在的 <Engine>。

    打包成 exe 后 __file__ 指向 PyInstaller 的解包目录，所以按 sys.executable（exe 本身）所在位置找；
    exe 放在 <安装目录>\\ 下时命中的是 <安装目录>\\Engine。
    """
    for cand in (preferred, os.environ.get("S2A_ENGINE")):
        if engine_ok(cand):
            return Path(cand)
    here = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else HERE.parents[1]
    for cand in (here, here / "Engine", here.parent / "Engine"):
        if engine_ok(cand):
            return cand
    return here


ENGINE = find_engine()
IMAGES = ("romfs.img",)   # 只有 romfs 是单文件镜像；着色器（shaders/）是逐文件，直接硬链接过去


class BuildError(Exception):
    pass


def load_engine(engine=None):
    """载入 <Engine>/pack/romfs_image.py（现拼 romfs.img 用的；着色器是逐文件形态，不拼镜像）。"""
    packdir = Path(engine or ENGINE) / "pack"
    rp = packdir / "romfs_image.py"
    if not rp.is_file():
        raise BuildError("找不到 %s（引擎目录不对）" % rp)
    if str(packdir) not in sys.path:
        sys.path.insert(0, str(packdir))
    import romfs_image
    return romfs_image


def read_index(pack):
    with open(os.path.join(pack, "index.tsv"), encoding="utf-8") as f:
        return [l.rstrip("\n").split("\t") for l in f if l.strip()]


SAMPLE_TOTAL = 64 << 20   # 估算时总共试压多少字节（64 MB：zlib 6 约 2 秒，玩家等得起）
SAMPLE_PIECE = 3 << 20    # 单次从文件里读多少做样本（大文件不整读，否则一个大类就吃光预算）
WORTH_RATIO = 0.85        # 压缩后 < 原来的 85%（省 15% 以上）就算值得压


def _kinds(pack):
    """按扩展名归堆：[ext] = [(路径, 字节)]；顺带算总字节。romfs.img 已按 #329 拆成 romfs.files/，这里遍历的是真实文件。"""
    kinds, raw = {}, 0
    for dirpath, _dirs, names in os.walk(pack):
        for name in names:
            p = os.path.join(dirpath, name)
            try:
                n = os.path.getsize(p)
            except OSError:
                continue
            raw += n
            k = kinds.setdefault(os.path.splitext(name)[1].lower() or "(无扩展名)", [])
            k.append((p, n))
    return kinds, raw


def _probe(path, size, cap):
    """只读 cap 字节去试压：小文件整读；大文件在文件里均匀取 8 段拼接（只读开头会把已压过的资源误判成可压）。"""
    with open(path, "rb") as f:
        if size <= cap:
            return f.read()
        n = 8
        seg = max(cap // n, 1 << 14)
        out = b""
        for k in range(n):
            f.seek(max(0, (size - seg) * k // n))
            out += f.read(seg)
        return out


def estimate(pack, budget=None, tick=None):
    """估算这套资源包打成压缩包能省多少：按扩展名分层抽样、现场试压（zlib level 6），不落盘、不改产物。

    预算按类均分（类别少的包每类能多抽），层内按文件体积降序抽；没抽到的类按已测的整体压缩率折算。
    返回：
      {rawBytes, packedBytes, savedBytes, ratio, sampledBytes, byKind: [{ext, files, bytes, sampled, ratio, packed}],
       worthIt, verdict}
    verdict：worth（省得多，值得压）/ thin（省得少，别折腾）/ none（没有可压的东西）
    tick：每压完一个样本调一次（作业拿它响应「停止」）。
    """
    budget = budget or SAMPLE_TOTAL
    kinds, raw = _kinds(pack)
    if not raw:
        return dict(rawBytes=0, packedBytes=0, savedBytes=0, ratio=1.0, sampledBytes=0, byKind=[],
                    worthIt=False, verdict="none")
    per_kind = max(2 << 20, budget // max(len(kinds), 1))
    rows, sampled = [], 0
    for ext, items in sorted(kinds.items(), key=lambda kv: -sum(n for _, n in kv[1])):
        picked = packed = 0
        for p, n in sorted(items, key=lambda x: -x[1]):
            cap = min(per_kind - picked, budget - sampled, SAMPLE_PIECE)
            if cap <= 0:
                break
            try:
                b = _probe(p, n, cap)   # 抽样只读，不动文件
            except OSError:
                continue
            if not b:
                continue
            picked += len(b)
            sampled += len(b)
            packed += len(zlib.compress(b, 6))
            if tick:
                tick()
        rows.append(dict(ext=ext, files=len(items), bytes=sum(n for _, n in items),
                         sampled=picked, ratio=(packed / picked) if picked else None))
    measured = [r for r in rows if r["ratio"] is not None]
    fallback = (sum(r["sampled"] * r["ratio"] for r in measured) / sum(r["sampled"] for r in measured)
                if measured else 1.0)
    for r in rows:   # 没抽到的类按已测整体比率折算
        if r["ratio"] is None:
            r["ratio"] = fallback
        r["packed"] = int(r["bytes"] * r["ratio"])
    packed_total = min(sum(r["packed"] for r in rows), raw)   # 压不动时别报「压缩后比原来还大」
    ratio = packed_total / raw
    saved = raw - packed_total
    worth = ratio < WORTH_RATIO
    return dict(rawBytes=raw, packedBytes=packed_total, savedBytes=saved, ratio=ratio, sampledBytes=sampled,
                byKind=sorted(rows, key=lambda r: -r["bytes"]), worthIt=worth,
                verdict="worth" if worth else "thin")


def place(src, dst):
    """同卷硬链接（零额外占用），跨卷退回复制；先写 .part 再改名，中断不会留下半份。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    if os.path.lexists(tmp):
        os.remove(tmp)
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def scan_games(games_dir):
    """→ [{name, gid, pack, bytes, ok, why}]，按目录名排序。"""
    out = []
    gdir_root = os.fspath(games_dir)
    if not os.path.isdir(gdir_root):
        return out
    for name in sorted(os.listdir(gdir_root)):
        gdir = os.path.join(gdir_root, name)
        if not os.path.isdir(gdir):
            continue
        pack = os.path.join(gdir, "image", "ios", "pack")
        meta = os.path.join(gdir, "game.json")
        item = dict(name=name, gid="", pack=pack, bytes=0, ok=False, why="")
        try:
            with open(meta, encoding="utf-8") as f:
                item["gid"] = (json.load(f).get("id") or "").upper()
        except (OSError, ValueError):
            item["why"] = "没有 game.json"
            out.append(item)
            continue
        if len(item["gid"]) != 16:
            item["why"] = "game.json 里没有合法游戏号"
        elif not os.path.isfile(os.path.join(pack, "index.tsv")):
            item["why"] = "没有资源包（image/ios/pack/index.tsv）"
        else:
            try:
                item["bytes"] = sum(int(r[1]) for r in read_index(pack))
                item["ok"] = True
            except (OSError, ValueError, IndexError):
                item["why"] = "index.tsv 读不了"
        out.append(item)
    return out


def build_one(romfs_mod, item, out_root, emit=lambda frac, text: None, compress=False):
    """生成一款；emit(0~1, 文字) 报进度。返回落盘字节数。

    compress=True：romfs.img 写成 romfs.img.gz（分块 gzip，见 gzimage.py），其余文件照旧。
    index.tsv / manifest.json 原样带过去——里面记的还是解压后的逻辑长度与 sha256，手机端按逻辑长度核。
    """
    pack, gid = item["pack"], item["gid"]
    dst = os.path.join(out_root, gid, "资源包")
    shutil.rmtree(os.path.join(out_root, gid), ignore_errors=True)
    os.makedirs(dst)
    rs = read_index(pack)
    total = sum(int(r[1]) for r in rs) or 1
    acc = 0
    made = {}
    try:
        for k, (rel, size, sha) in enumerate(rs, 1):
            n = int(size)
            name = os.path.basename(rel)
            src = os.path.join(pack, rel)
            if name in IMAGES and not os.path.isfile(src):
                def prog(pos, _base=acc, _rel=rel, _n=n):
                    emit(min(0.999, (_base + pos) / total),
                         "拼 %s  %.2f / %.2f GB" % (_rel, pos / 1e9, _n / 1e9))

                base = os.path.join(pack, rel[: -len(".img")] + ".files")
                if not os.path.isdir(base):
                    raise BuildError("index.tsv 列了 %s，但包里既没有它也没有 %s/" % (rel, rel[:-4]))
                made[rel] = romfs_mod.materialize(pack, rel, n, sha, say=lambda *_: None, progress=prog)
                src = made[rel]
            if compress and name in IMAGES:
                # 压缩镜像：直接写进产物（不落明文），进度按块折算到这一项的总字节
                def cprog(done_blocks, total_blocks, _base=acc, _rel=rel, _n=n):
                    emit(min(0.999, (_base + _n * done_blocks / max(total_blocks, 1)) / total),
                         "压缩 %s  第 %d / %d 块" % (_rel, done_blocks, total_blocks))

                gzimage.pack_image(src, gzimage.blob_name(os.path.join(dst, rel)), tick=cprog)
            else:
                place(src, os.path.join(dst, rel))
            acc += n
            emit(min(0.999, acc / total), "写入文件 %d / %d" % (k, len(rs)))
        for f in ("index.tsv", "manifest.json"):
            place(os.path.join(pack, f), os.path.join(dst, f))
        emit(1.0, "完成  %.2f GB" % (acc / 1e9))
        return acc
    finally:
        for p in made.values():
            try:
                os.remove(p)
            except OSError:
                pass


def default_games_dir():
    """没给参数时：用记住的路径；没有就试常见位置。"""
    saved = Path.home() / ".s2arm-phone-pack.json"
    try:
        cfg = json.loads(saved.read_text(encoding="utf-8"))
        if cfg.get("games") and os.path.isdir(cfg["games"]):
            return cfg["games"]
    except (OSError, ValueError):
        pass
    for cand in (r"G:\Games\Switch\Ios\games", str(Path.home() / "S2Arm" / "games")):
        if os.path.isdir(cand):
            return cand
    return ""


def default_out_dir(games_dir):
    parent = os.path.dirname(os.path.abspath(games_dir)) if games_dir else str(Path.home())
    return os.path.join(parent, "phone", "games")


def main(argv):
    games = argv[1] if len(argv) > 1 else default_games_dir()
    out = argv[2] if len(argv) > 2 else default_out_dir(games)
    if not games or not os.path.isdir(games):
        print("没有找到游戏库目录，用法：make_phone_pack.py <游戏库目录> [输出目录]", file=sys.stderr)
        return 2
    mods = load_engine()
    items = [i for i in scan_games(games) if i["ok"]]
    if not items:
        print("这个目录下没有可生成的资源包：%s" % games, file=sys.stderr)
        return 1
    ok = 0
    for it in items:
        print("== %s -> %s" % (it["name"], it["gid"]))
        try:
            build_one(mods, it, out, lambda frac, text, _n=it["name"]: print("   %5.1f%% %s" % (frac * 100, text)))
            ok += 1
        except Exception as e:   # noqa: BLE001
            print("   【红】%s：%s" % (it["name"], e), file=sys.stderr)
    print("\n完成 %d / %d 款 -> %s" % (ok, len(items), out))
    return 0 if ok == len(items) else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:   # noqa: BLE001
        pass
    sys.exit(main(sys.argv))
