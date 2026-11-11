#!/usr/bin/env python3
"""S2Arm 手机资源包生成器（图形界面）

把 <游戏库>/<游戏>/image/ios/pack 整理成手机能直接玩的形态：
  <输出>/<游戏号>/资源包   ==   手机上 Documents/games/<游戏号>/资源包
镜像（index.tsv 里的 romfs.img / shaders.img）从 *.files/ 现拼，其余文件同卷硬链接。
核心逻辑在 make_phone_pack.py，本文件只管界面。
"""
import json
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import make_phone_pack as core   # noqa: E402

CFG = Path.home() / ".s2arm-phone-pack.json"


def load_cfg():
    try:
        return json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cfg(games, out, engine=""):
    try:
        CFG.write_text(json.dumps({"games": games, "out": out, "engine": engine}, ensure_ascii=False),
                       encoding="utf-8")
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("S2Arm 手机资源包生成器")
        self.geometry("840x650")
        self.minsize(720, 480)
        self.q = queue.Queue()
        self.items = []
        self.rows = []
        self.running = False
        self._build()
        self.after(80, self._drain)
        self.after(120, self.scan)

    def _build(self):
        pad = dict(padx=6, pady=4)
        cfg = load_cfg()
        games = cfg.get("games") or core.default_games_dir()
        out = cfg.get("out") or core.default_out_dir(games)

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        self.games_var = tk.StringVar(value=games)
        self.out_var = tk.StringVar(value=out)
        self.engine_var = tk.StringVar(value=cfg.get("engine") or str(core.ENGINE))
        for r, (label, var) in enumerate((("游戏库", self.games_var), ("输出目录", self.out_var),
                                          ("引擎目录", self.engine_var))):
            ttk.Label(top, text=label, width=8).grid(row=r, column=0, sticky="w")
            ttk.Entry(top, textvariable=var).grid(row=r, column=1, sticky="ew", padx=(0, 6), pady=2)
            ttk.Button(top, text="浏览…", width=7, command=lambda v=var: self._browse(v)).grid(row=r, column=2)
            top.columnconfigure(1, weight=1)
        self.engine_hint = ttk.Label(top, text="", foreground="#666")
        self.engine_hint.grid(row=3, column=1, columnspan=2, sticky="w")
        self.engine_var.trace_add("write", lambda *_: self._check_engine())
        self._check_engine()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6)
        ttk.Button(bar, text="重新扫描", command=self.scan).pack(side="left")
        self.sum_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.sum_var).pack(side="left", padx=10)
        ttk.Button(bar, text="打开输出目录", command=self._open_out).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=6, pady=6)
        canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.inner.columnconfigure(0, weight=1)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=6, pady=(0, 6))
        self.total = ttk.Progressbar(bottom, maximum=100.0)
        self.total.pack(fill="x", pady=(0, 4))
        self.status_var = tk.StringVar(value="准备就绪")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w")
        self.go = ttk.Button(bottom, text="开始生成", command=self.start)
        self.go.pack(anchor="e", pady=(4, 0))

    def _browse(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(os.path.normpath(d))
            if var is self.games_var:
                self.out_var.set(core.default_out_dir(d))
                self.scan()

    def _check_engine(self):
        """引擎目录是否有效：有 pack/romfs_image.py 才算数（拼 romfs.img 要用它）。"""
        ok = core.engine_ok(self.engine_var.get().strip())
        self.engine_hint.configure(
            text="✓ 引擎目录可用" if ok else "× 这里没有 pack/romfs_image.py，请选对引擎目录（或设 S2A_ENGINE）",
            foreground="#2e7d32" if ok else "#c62828")
        return ok

    def _open_out(self):
        d = self.out_var.get()
        if os.path.isdir(d):
            os.startfile(d)
        else:
            messagebox.showinfo("提示", "输出目录还不存在：\n%s" % d)

    # ---- 列表 ----

    def scan(self):
        if self.running:
            return
        for w in self.inner.winfo_children():
            w.destroy()
        self.rows = []
        self.items = core.scan_games(self.games_var.get())
        if not self.items:
            ttk.Label(self.inner, text="这个目录下没有游戏（应有 <游戏>/game.json 与 <游戏>/image/ios/pack/）").grid(
                row=0, column=0, sticky="w", pady=6)
            self.sum_var.set("")
            return
        total, okn = 0, 0
        for i, it in enumerate(self.items):
            fr = ttk.Frame(self.inner)
            fr.grid(row=i, column=0, sticky="ew", pady=1)
            fr.columnconfigure(2, weight=1)
            ttk.Label(fr, text=it["name"], width=32, anchor="w").grid(row=0, column=0, sticky="w")
            ttk.Label(fr, text=it["gid"] or "—", width=17, anchor="w").grid(row=0, column=1, sticky="w")
            pb = ttk.Progressbar(fr, maximum=100.0)
            pb.grid(row=0, column=2, sticky="ew", padx=6)
            st = ttk.Label(fr, text=it["why"] or "等待", width=24, anchor="w")
            st.grid(row=0, column=3, sticky="w")
            self.rows.append((pb, st))
            if it["ok"]:
                okn += 1
                total += it["bytes"]
        self.sum_var.set("共 %d 款，可生成 %d 款，合计 %.1f GiB" % (len(self.items), okn, total / 2 ** 30))
        self.status_var.set("点「开始生成」；输出到 %s" % self.out_var.get())

    # ---- 生成 ----

    def start(self):
        if self.running:
            return
        todo = [(i, it) for i, it in enumerate(self.items) if it["ok"]]
        if not todo:
            messagebox.showwarning("没有可生成的", "没有找到完整的资源包（需要 image/ios/pack/index.tsv）")
            return
        if not self._check_engine():
            messagebox.showerror("引擎目录不对",
                                 "拼 romfs.img 要用引擎里的 pack/romfs_image.py。\n"
                                 "请在上方「引擎目录」里选对位置（exe 旁边放 Engine 目录也行）。")
            return
        out = os.path.join(self.out_var.get(), "")
        try:
            os.makedirs(out, exist_ok=True)
            free = shutil.disk_usage(out).free
        except OSError as e:
            messagebox.showerror("输出目录不可用", str(e))
            return
        need = sum(it["bytes"] for _, it in todo)
        if free < need * 1.02 and not messagebox.askyesno(
                "空间可能不够", "需要约 %.1f GiB，输出盘只剩 %.1f GiB。\n仍然继续吗？" % (need / 2 ** 30, free / 2 ** 30)):
            return
        existing = [it["gid"] for _, it in todo if os.path.isdir(os.path.join(out, it["gid"]))]
        if existing and not messagebox.askyesno(
                "覆盖确认", "输出目录里已经有 %d 款（%s…），会先删掉再生成。继续吗？" % (len(existing), existing[0])):
            return
        engine = self.engine_var.get().strip() or None
        save_cfg(self.games_var.get(), self.out_var.get(), engine or "")
        self.running = True
        self.go.configure(state="disabled")
        for pb, _ in self.rows:
            pb.configure(value=0.0)
        self.total.configure(value=0.0)
        self.status_var.set("正在生成…")
        threading.Thread(target=self._worker, args=(todo, need, out, engine), daemon=True).start()

    def _worker(self, todo, need, out, engine):
        try:
            mod = core.load_engine(engine)
        except Exception as e:   # noqa: BLE001
            self.q.put(("fatal", str(e)))
            return
        done, failed = 0, 0
        for i, it in todo:
            self.q.put(("msg", "正在处理 %s（%s）" % (it["name"], it["gid"])))
            try:
                done += core.build_one(mod, it, out,
                                       lambda frac, text, _i=i: self.q.put(("pct", _i, frac, text)))
            except Exception as e:   # noqa: BLE001
                failed += 1
                self.q.put(("fail", i, str(e)))
                done += it["bytes"]
            self.q.put(("total", done / need if need else 1.0))
        self.q.put(("finish", failed))

    # ---- 界面刷新 ----

    def _drain(self):
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "pct":
                    _, i, frac, text = m
                    pb, st = self.rows[i]
                    pb.configure(value=max(0.0, min(100.0, frac * 100.0)))
                    st.configure(text=text)
                elif kind == "total":
                    self.total.configure(value=max(0.0, min(100.0, m[1] * 100.0)))
                elif kind == "msg":
                    self.status_var.set(m[1])
                elif kind == "fail":
                    _, i, why = m
                    pb, st = self.rows[i]
                    pb.configure(value=0.0)
                    st.configure(text="失败：" + why[:44])
                elif kind == "fatal":
                    self.running = False
                    self.go.configure(state="normal")
                    self.status_var.set("引擎载入失败")
                    messagebox.showerror("引擎载入失败", m[1])
                elif kind == "finish":
                    failed = m[1]
                    self.running = False
                    self.go.configure(state="normal")
                    self.status_var.set("完成" if not failed else "完成，%d 款失败" % failed)
                    messagebox.showinfo("完成", "生成结束。\n\n把输出目录里的各个「游戏号」文件夹拷进手机 App 的 Documents/games/ 下。")
        except queue.Empty:
            pass
        self.after(80, self._drain)


if __name__ == "__main__":
    App().mainloop()
