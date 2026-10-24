#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UartSender —— 把命令文本逐行"敲"进其它窗口（串口终端 / 命令行窗口）。

界面布局（与设计草图一致）：

    ┌─────────────────────────────────────────────┬──────────┐
    │  状态栏 / 当前目标                            │  发送    │
    ├─────────────────────────────────────────────┤          │
    │ [ txt_path .......................] [ open ] │   (○)    │
    ├──────────┬──────────────────────────────────┤          │
    │ uart0    │  cmd 0                           │          │
    │ uart1    ├──────────────────────────────────┤          │
    │ uart2    │  cmd 1                           │   设置   │
    ├──────────┤──────────────────────────────────┤          │
    │ # 注释…  │  cmd 2                           │          │
    └──────────┴──────────────────────────────────┴──────────┘

文件格式（.txt）::

    ##>0                 # 之后的内容显示在 cmd0，发送到 uart0 绑定的窗口
    # 注释行不会被发送，会出现在左侧"注释 / 定位"列表里
    ls -l /dev/ttyUSB0
    ##>1
    uname -a

两种显示模式（设置里切换，或点顶部"拆分/原样"按钮）：
    * 拆分显示：按 ##>n 分到 cmd0 ~ cmd4 五栏，各自绑一个窗口；
    * 不拆分  ：整份文件原样显示在一栏，发送时遇到 ##>n 自动切换目标窗口。

配置文件：与程序同级的 UartSender.json（最近打开的 10 个文件、显示模式、发送参数）。

快捷键：Ctrl+S 保存、Ctrl+Shift+S 另存为、Ctrl+O 打开、
        Ctrl+Enter 发送（选中行 / 光标行）、Shift+Ctrl+Enter 发送整个 cmd 页。
"""

from __future__ import annotations

import json
import sys
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

import winutil

APP_NAME = "UartSender"
APP_TITLE = "UartSender · 命令发送器"
PANE_COUNT = 5
CFG_VERSION = 2

NEWLINE_CHOICES = [
    ("CRLF  (\\r\\n)", "CRLF"),
    ("CR  (\\r)", "CR"),
    ("LF  (\\n)", "LF"),
    ("不发送换行", "NONE"),
]
NEWLINE_MAP = {"CRLF": "\r\n", "CR": "\r", "LF": "\n", "NONE": ""}
METHOD_CHOICES = [
    ("后台消息 WM_CHAR（推荐）", "message"),
    ("前台模拟键盘 SendInput", "input"),
]

DEFAULT_CFG = {
    "line_interval": 200,      # 行间隔 ms
    "char_interval": 10,       # 字符间隔 ms
    "newline": "CRLF",         # 每行结尾发送的换行符
    "send_method": "message",  # message | input
    "focus_mode": "keep",      # none=不切换 | temp=临时切前台(发完切回) | keep=保持在前台
    "always_on_top": True,     # 本窗口始终置顶
    "skip_empty": True,        # 跳过空行
    "keydown_compat": False,   # 消息模式附带 WM_KEYDOWN/KEYUP
    "auto_advance": True,      # 发送后自动选中下一行（逐行执行）
    "display_mode": "single",  # split=拆分显示(多栏)  single=原样显示(单栏)
    "opacity": 1.0,            # 界面透明度 0.3 ~ 1.0
    "recent_files": [],        # 最近打开的 10 个文件
    "cfg_version": CFG_VERSION,
}

MODE_SPLIT = "split"
MODE_SINGLE = "single"
DISPLAY_CHOICES = [
    ("拆分显示：按 ##>n 分到 cmd0 / cmd1 / … / cmd4", MODE_SPLIT),
    ("不拆分：整份文件原样显示，遇 ##>n 自动切换目标", MODE_SINGLE),
]

FOCUS_NONE, FOCUS_TEMP, FOCUS_KEEP = "none", "temp", "keep"
FOCUS_CHOICES = [
    ("不切换：纯后台消息（终端类程序收不到）", FOCUS_NONE),
    ("临时切换：切到目标窗口，发完切回本程序", FOCUS_TEMP),
    ("保持在前台：切过去，发完停在目标窗口", FOCUS_KEEP),
]
MAX_RECENT = 10

# ##>n 指令行（n = 0..4），发送时用来切换目标窗口
DIR_RE = re.compile(r"^\s*##>\s*(\d+)\s*(.*)$")
# 注释分组标题：形如 #======测试1==== ，# 后面跟 1 个或多个 =
GROUP_RE = re.compile(r"^\s*#\s*=+")

BG = "#f4f5f7"
PANEL = "#ffffff"
LINE = "#c9ced6"
ACCENT = "#1a73e8"
ACCENT_DARK = "#0b57d0"
OK_GREEN = "#188038"
WARN = "#e8710a"
TXT = "#202124"
GREY = "#5f6368"


# --------------------------------------------------------------------------- #
# 配置文件（默认与程序同级目录，随程序一起拷走；不可写时退回用户目录）
# --------------------------------------------------------------------------- #
CONFIG_NAME = "UartSender.json"
CONFIG_PATH = None          # 运行时确定


def app_dir() -> str:
    """程序所在目录：打包后是 exe 所在目录，源码运行时是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    global CONFIG_PATH
    if CONFIG_PATH is None:
        CONFIG_PATH = os.path.join(app_dir(), CONFIG_NAME)
    return CONFIG_PATH


def _fallback_config_path() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, APP_NAME)
    else:
        folder = os.path.expanduser("~/.config/" + APP_NAME)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.expanduser("~")
    return os.path.join(folder, CONFIG_NAME)


def _legacy_config_path() -> str:
    """旧版本把配置放在 %APPDATA%，首次运行时自动迁移过来。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME, "config.json")
    return os.path.expanduser("~/.config/" + APP_NAME + "/config.json")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_config() -> dict:
    cfg = dict(DEFAULT_CFG)
    data = _read_json(config_path())
    migrated = False
    if data is None:
        data = _read_json(_legacy_config_path())
        migrated = data is not None
    if data:
        for key in DEFAULT_CFG:
            if key in data:
                cfg[key] = data[key]
        # 兼容旧版只有 activate_window 布尔值的配置：
        # 旧版"不切换"对终端类程序是无效的，这里直接升级成更靠谱的"临时切换"
        if "focus_mode" not in data and "activate_window" in data:
            cfg["focus_mode"] = FOCUS_KEEP if data["activate_window"] else FOCUS_TEMP
    if cfg.get("focus_mode") not in (FOCUS_NONE, FOCUS_TEMP, FOCUS_KEEP):
        cfg["focus_mode"] = FOCUS_TEMP
    cfg.pop("activate_window", None)          # 旧字段，迁移后丢弃
    if not isinstance(cfg.get("recent_files"), list):
        cfg["recent_files"] = []
    cfg["recent_files"] = [str(p) for p in cfg["recent_files"]][:MAX_RECENT]
    if cfg.get("display_mode") not in (MODE_SPLIT, MODE_SINGLE):
        cfg["display_mode"] = MODE_SPLIT

    # v2：一次性把老的默认值升级成"原样显示 + 始终置顶 + 保持前台"
    try:
        old_ver = int(data.get("cfg_version", 1)) if data else CFG_VERSION
    except (TypeError, ValueError):
        old_ver = 1
    if old_ver < CFG_VERSION:
        cfg["display_mode"] = DEFAULT_CFG["display_mode"]
        cfg["always_on_top"] = DEFAULT_CFG["always_on_top"]
        cfg["focus_mode"] = DEFAULT_CFG["focus_mode"]
        cfg["cfg_version"] = CFG_VERSION
        migrated = True
    try:
        cfg["opacity"] = min(1.0, max(0.3, float(cfg.get("opacity", 1.0))))
    except (TypeError, ValueError):
        cfg["opacity"] = 1.0
    if migrated:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> bool:
    """把配置写到程序同级目录；目录不可写时退回用户目录。"""
    global CONFIG_PATH
    payload = dict(cfg)
    payload["recent_files"] = list(cfg.get("recent_files", []))[:MAX_RECENT]
    for path in (config_path(), _fallback_config_path()):
        try:
            folder = os.path.dirname(path) or "."
            os.makedirs(folder, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            CONFIG_PATH = path
            return True
        except OSError:
            continue
    return False


def pick_font(root, candidates, fallback="TkDefaultFont"):
    fams = set(tkfont.families(root))
    for name in candidates:
        if name in fams:
            return name
    return fallback


# --------------------------------------------------------------------------- #
# 设置窗口
# --------------------------------------------------------------------------- #
class SettingsDialog(tk.Toplevel):
    def __init__(self, master: "App", cfg: dict):
        super().__init__(master)
        self.app = master
        self.cfg = dict(cfg)
        self.title("设置")
        self.transient(master)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.result = None

        body = tk.Frame(self, bg=BG, padx=18, pady=12)
        body.pack(fill="both", expand=True)

        self.v_line = tk.StringVar(value=str(cfg["line_interval"]))
        self.v_char = tk.StringVar(value=str(cfg["char_interval"]))
        self.v_nl = tk.StringVar(value=self._label_of(NEWLINE_CHOICES, cfg["newline"]))
        self.v_method = tk.StringVar(value=self._label_of(METHOD_CHOICES, cfg["send_method"]))
        self.v_focus = tk.StringVar(value=cfg.get("focus_mode", FOCUS_TEMP))
        self.v_top = tk.BooleanVar(value=bool(cfg.get("always_on_top", False)))
        self.v_adv = tk.BooleanVar(value=bool(cfg.get("auto_advance", True)))
        self.v_opacity = tk.DoubleVar(value=float(cfg.get("opacity", 1.0)) * 100.0)
        self.v_skip = tk.BooleanVar(value=bool(cfg["skip_empty"]))
        self.v_compat = tk.BooleanVar(value=bool(cfg["keydown_compat"]))
        self.v_mode = tk.StringVar(value=cfg.get("display_mode", MODE_SPLIT))

        row = 0
        tk.Label(body, text="文本显示方式（顶部“模式”按钮也能切换）", bg=BG, fg=TXT,
                 font=master.font_bold).grid(row=row, column=0, columnspan=2,
                                             sticky="w", pady=(0, 3))
        row += 1
        for text, value in DISPLAY_CHOICES:
            ttk.Radiobutton(body, text=text, value=value, variable=self.v_mode).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=0)
            row += 1
        tk.Frame(body, height=1, bg=LINE).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8)
        row += 1

        tk.Label(body, text="发送节奏", bg=BG, fg=TXT, font=master.font_bold).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1
        self._spin_row(body, row, "行间隔 (ms)", self.v_line, 0, 60000)
        row += 1
        self._spin_row(body, row, "字符间隔 (ms)", self.v_char, 0, 5000)
        row += 1
        tk.Label(body, text="每行结尾换行符", bg=BG, fg=TXT, font=master.font).grid(
            row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(body, textvariable=self.v_nl, values=[c[0] for c in NEWLINE_CHOICES],
                     state="readonly", width=24).grid(row=row, column=1, sticky="w", pady=6)
        row += 1
        tk.Frame(body, height=1, bg=LINE).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8)
        row += 1
        tk.Label(body, text="发送方式", bg=BG, fg=TXT, font=master.font_bold).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1
        ttk.Combobox(body, textvariable=self.v_method, values=[c[0] for c in METHOD_CHOICES],
                     state="readonly", width=28).grid(row=row, column=0, columnspan=2,
                                                      sticky="w", pady=4)
        row += 1
        tk.Label(body, text="发送时窗口焦点", bg=BG, fg=TXT, font=master.font_bold).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1
        for text, value in FOCUS_CHOICES:
            ttk.Radiobutton(body, text=text, value=value, variable=self.v_focus).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=0)
            row += 1
        tk.Label(body, text="界面透明度", bg=BG, fg=TXT, font=master.font).grid(
            row=row, column=0, sticky="w", pady=(6, 2))
        op_box = tk.Frame(body, bg=BG)
        op_box.grid(row=row, column=1, sticky="w", pady=(6, 2))
        self.scale_opacity = ttk.Scale(op_box, from_=30, to=100, orient="horizontal",
                                       variable=self.v_opacity, length=170,
                                       command=self._preview_opacity)
        self.scale_opacity.pack(side="left")
        self.lbl_opacity = tk.Label(op_box, text=f"{int(self.v_opacity.get())}%",
                                    bg=BG, fg=GREY, font=master.font_small, width=5)
        self.lbl_opacity.pack(side="left", padx=(6, 0))
        row += 1

        for text, var in (
            ("本窗口始终置顶（切到目标窗口后仍浮在最上面）", self.v_top),
            ("发送后自动选中下一行（可一路点发送逐行执行）", self.v_adv),
            ("跳过空行", self.v_skip),
            ("消息模式同时发送按键消息 WM_KEYDOWN/WM_KEYUP（个别程序需要）", self.v_compat),
        ):
            tk.Checkbutton(body, text=text, variable=var, bg=BG, fg=TXT, font=master.font,
                           activebackground=BG, anchor="w", highlightthickness=0,
                           bd=0).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
            row += 1

        hint = ("· MobaXterm / Windows Terminal 等只在窗口激活时处理按键，“不切换”会收不到命令；\n"
                "· 命令含中文、或终端仍无反应时，把“发送方式”改成前台模拟键盘。")
        tk.Label(body, text=hint, bg=BG, fg=GREY, font=master.font_small,
                 justify="left").grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 4))
        row += 1

        btns = tk.Frame(body, bg=BG)
        btns.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))
        tk.Button(btns, text="恢复默认", width=9, font=master.font,
                  command=self._reset).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="取消", width=8, font=master.font,
                  command=self.destroy).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="确定", width=8, font=master.font, bg=ACCENT, fg="white",
                  activebackground=ACCENT_DARK, activeforeground="white",
                  command=self._ok).pack(side="left")

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._center(master)
        self.grab_set()
        self.path_entry = None

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _label_of(choices, key):
        for label, value in choices:
            if value == key:
                return label
        return choices[0][0]

    @staticmethod
    def _value_of(choices, label, fallback):
        for lb, value in choices:
            if lb == label:
                return value
        return fallback

    def _spin_row(self, parent, row, text, var, lo, hi):
        tk.Label(parent, text=text, bg=BG, fg=TXT, font=self.app.font).grid(
            row=row, column=0, sticky="w", pady=4)
        sp = tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=10,
                        font=self.app.font, justify="right")
        sp.grid(row=row, column=1, sticky="w", pady=4)
        return sp

    def _preview_opacity(self, _value=None):
        try:
            percent = max(30.0, min(100.0, float(self.v_opacity.get())))
        except (TypeError, ValueError):
            percent = 100.0
        try:
            self.lbl_opacity.configure(text=f"{int(round(percent))}%")
        except tk.TclError:
            pass
        try:
            self.app.attributes("-alpha", percent / 100.0)
        except tk.TclError:
            pass

    def _center(self, master):
        self.update_idletasks()
        x = master.winfo_rootx() + max(0, (master.winfo_width() - self.winfo_width()) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - self.winfo_height()) // 3)
        self.geometry(f"+{x}+{y}")

    def _reset(self):
        self.v_line.set(str(DEFAULT_CFG["line_interval"]))
        self.v_char.set(str(DEFAULT_CFG["char_interval"]))
        self.v_nl.set(self._label_of(NEWLINE_CHOICES, DEFAULT_CFG["newline"]))
        self.v_method.set(self._label_of(METHOD_CHOICES, DEFAULT_CFG["send_method"]))
        self.v_focus.set(DEFAULT_CFG["focus_mode"])
        self.v_top.set(DEFAULT_CFG["always_on_top"])
        self.v_adv.set(DEFAULT_CFG["auto_advance"])
        self.v_opacity.set(DEFAULT_CFG["opacity"] * 100)
        self._preview_opacity()
        self.v_skip.set(DEFAULT_CFG["skip_empty"])
        self.v_compat.set(DEFAULT_CFG["keydown_compat"])
        self.v_mode.set(DEFAULT_CFG["display_mode"])

    def _ok(self):
        try:
            line = int(float(self.v_line.get()))
            char = int(float(self.v_char.get()))
        except ValueError:
            messagebox.showwarning("设置", "间隔必须是数字（毫秒）。", parent=self)
            return
        self.cfg.update({
            "line_interval": max(0, min(line, 60000)),
            "char_interval": max(0, min(char, 5000)),
            "newline": self._value_of(NEWLINE_CHOICES, self.v_nl.get(), "CRLF"),
            "send_method": self._value_of(METHOD_CHOICES, self.v_method.get(), "message"),
            "focus_mode": self.v_focus.get(),
            "always_on_top": bool(self.v_top.get()),
            "auto_advance": bool(self.v_adv.get()),
            "opacity": round(min(100.0, max(30.0, float(self.v_opacity.get())))) / 100.0,
            "skip_empty": bool(self.v_skip.get()),
            "keydown_compat": bool(self.v_compat.get()),
            "display_mode": self.v_mode.get(),
        })
        self.result = self.cfg
        self.destroy()


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()

        self.font_name = pick_font(self, ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                                          "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Zen Hei",
                                          "DejaVu Sans"])
        self.mono_name = pick_font(self, ["Consolas", "Cascadia Mono", "DejaVu Sans Mono",
                                          "Menlo", "Courier New"], "TkFixedFont")
        self.font = tkfont.Font(family=self.font_name, size=9)
        self.font_bold = tkfont.Font(family=self.font_name, size=9, weight="bold")
        self.font_small = tkfont.Font(family=self.font_name, size=8)
        self.font_btn = tkfont.Font(family=self.font_name, size=10, weight="bold")
        self.font_mono = tkfont.Font(family=self.mono_name, size=10)

        self.title(APP_TITLE)
        self.geometry("1060x700")
        self.minsize(880, 560)
        self.configure(bg=BG)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"1060x700+{max(0, (sw - 1060) // 2)}+{max(0, (sh - 700) // 3)}")

        # 运行状态
        self.file_path = None
        self.encoding = "utf-8"
        self.dirty = False
        self.bindings = [None] * PANE_COUNT      # 每个 cmd 页 / uart 槽位的 hwnd
        self.bind_titles = [""] * PANE_COUNT
        self.active_pane = 0                     # 当前编辑（发送）页
        self.slot = 0                            # uart 列表选中的槽位（圆圈绑定目标）
        self.comment_items = []
        self._comment_index = {}
        self._collapsed_groups = set()
        self._updating_list = False
        self.sending = False
        self.stop_flag = threading.Event()
        self.msgq = queue.Queue()
        self._refresh_job = None
        self._drag_overlay = None
        self.display_mode = self.cfg.get("display_mode", MODE_SPLIT)
        self.pane_frames = []

        self._build_ui()
        self._bind_shortcuts()
        self._set_window_icon()
        self._apply_topmost()
        self._apply_opacity()
        self._refresh_recent()
        self._refresh_uart_list()
        self._apply_display_mode(self.display_mode, convert=False)
        self._set_pane(0)
        self._update_title()
        self.after(80, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.path_entry.focus_set()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=252)
        self.columnconfigure(1, weight=1, minsize=320)
        self.columnconfigure(2, weight=0, minsize=106)
        self.rowconfigure(2, weight=1)

        # ---- 顶部状态条 --------------------------------------------------
        top = tk.Frame(self, bg=PANEL, bd=1, relief="solid", height=38)
        top.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=(8, 4), pady=(8, 0))
        top.grid_propagate(False)
        top.columnconfigure(1, weight=1)
        self.target_badge = tk.Label(top, text="目标：cmd0", bg=ACCENT, fg="white",
                                     font=self.font_small, padx=8, pady=2)
        self.target_badge.grid(row=0, column=0, sticky="w", padx=(8, 6), pady=8)
        self.status_var = tk.StringVar(value="就绪 · 点击 open 打开命令文件")
        tk.Label(top, textvariable=self.status_var, bg=PANEL, fg=TXT, font=self.font,
                 anchor="w").grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.btn_mode = tk.Button(top, text="拆分", font=self.font_small, width=6,
                                  command=self.on_toggle_mode)
        self.btn_mode.grid(row=0, column=2, sticky="e", padx=(0, 8))

        # ---- 文件路径行（可下拉查看最近打开的文件）------------------------
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(8, 4), pady=(6, 0))
        bar.columnconfigure(0, weight=1)
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Combobox(bar, textvariable=self.path_var, font=self.font_mono,
                                       values=[], height=MAX_RECENT)
        self.path_entry.grid(row=0, column=0, sticky="ew", ipady=1)
        self.path_entry.bind("<Return>", self._on_path_enter)
        self.path_entry.bind("<<ComboboxSelected>>", self._on_recent_selected)
        self.path_entry.bind("<Button-3>", self._on_recent_menu)
        self.btn_open = tk.Button(bar, text="open", width=8, font=self.font,
                                  command=self.on_open)
        self.btn_open.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # ---- 左侧：uart 列表 + 注释定位 ----------------------------------
        left = tk.Frame(self, bg=PANEL, bd=1, relief="solid")
        left.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=8)
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        self.uart_list = tk.Listbox(left, height=PANE_COUNT, font=self.font, bd=0,
                                    relief="flat",
                                    activestyle="none", exportselection=False,
                                    highlightthickness=0, bg=PANEL, fg=TXT,
                                    selectbackground="#dbeafe", selectforeground=TXT,
                                    justify="left")
        self.uart_list.grid(row=0, column=0, sticky="ew", padx=2, pady=(4, 2))
        self.uart_list.bind("<<ListboxSelect>>", self._on_uart_select)
        self.uart_list.bind("<Double-Button-1>", self._on_uart_double)
        self.uart_list.bind("<Button-3>", self._on_uart_menu)

        tk.Frame(left, height=1, bg=LINE).grid(row=1, column=0, sticky="ew", padx=6, pady=2)
        cbox = tk.Frame(left, bg=PANEL)
        cbox.grid(row=2, column=0, sticky="nsew")
        cbox.rowconfigure(1, weight=1)
        cbox.columnconfigure(0, weight=1)
        tk.Label(cbox, text="注释 / 定位（双击跳转，点 ▶ 展开）", bg=PANEL, fg=GREY,
                 font=self.font_small, anchor="w").grid(row=0, column=0, columnspan=2,
                                                        sticky="ew", padx=6, pady=(2, 0))
        style = ttk.Style(self)
        try:
            style.layout("Comment.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        style.configure("Comment.Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=GREY, borderwidth=0,
                        rowheight=self.font_small.metrics("linespace") + 5)
        style.map("Comment.Treeview", background=[("selected", "#dbeafe")],
                  foreground=[("selected", TXT)])
        self.comment_tree = ttk.Treeview(cbox, show="tree", selectmode="browse",
                                         style="Comment.Treeview")
        self.comment_tree.grid(row=1, column=0, sticky="nsew", padx=(2, 0), pady=(0, 2))
        csb = ttk.Scrollbar(cbox, orient="vertical", command=self.comment_tree.yview)
        csb.grid(row=1, column=1, sticky="ns")
        self.comment_tree.configure(yscrollcommand=csb.set)
        self.comment_tree.bind("<Double-Button-1>", self._on_comment_double)
        self.comment_tree.bind("<ButtonRelease-1>", self._remember_open_state)
        self.comment_tree.tag_configure("group", foreground=ACCENT_DARK,
                                        background="#eef3fd", font=self.font_bold)
        self.comment_tree.tag_configure("item", foreground=GREY)

        # ---- 中间：cmd0 ~ cmd4 -------------------------------------------
        self.paned = tk.PanedWindow(self, orient="vertical", bd=0, sashwidth=8,
                                    bg=BG, sashrelief="flat", showhandle=False)
        self.paned.grid(row=2, column=1, sticky="nsew", padx=6, pady=8)
        self.texts, self.headers, self.bind_labels = [], [], []
        self.pane_frames = []
        for i in range(PANE_COUNT):
            frame = tk.Frame(self.paned, bg=PANEL, bd=1, relief="solid")
            frame.rowconfigure(1, weight=1)
            frame.columnconfigure(0, weight=1)
            self.pane_frames.append(frame)

            head = tk.Frame(frame, bg=PANEL)
            head.grid(row=0, column=0, columnspan=2, sticky="ew")
            head.columnconfigure(1, weight=1)
            label = tk.Label(head, text=f"cmd {i}", bg=PANEL, fg=TXT, font=self.font_bold,
                             anchor="w", padx=8)
            label.grid(row=0, column=0, sticky="w", pady=2)
            bind_label = tk.Label(head, text="未绑定窗口", bg=PANEL, fg=GREY,
                                  font=self.font_small, anchor="e", padx=8)
            bind_label.grid(row=0, column=1, sticky="e")
            tk.Frame(frame, height=1, bg=LINE).grid(row=0, column=0, columnspan=2,
                                                    sticky="sew")

            txt = tk.Text(frame, font=self.font_mono, undo=True, wrap="none",
                          bd=0, highlightthickness=0, bg="#ffffff", fg="#1f1f1f",
                          insertbackground=ACCENT, selectbackground="#cfe3ff",
                          selectforeground="#000000", exportselection=False,
                          inactiveselectbackground="#cfe3ff",
                          padx=8, pady=4, spacing1=1, spacing3=1, tabs="4c")
            txt.grid(row=1, column=0, sticky="nsew")
            vsb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            vsb.grid(row=1, column=1, sticky="ns")
            txt.configure(yscrollcommand=vsb.set)
            # 5 栏时省掉横向滚动条（太占高度），改成 Shift+滚轮 横向滚动
            txt.bind("<Shift-MouseWheel>", lambda e, t=txt: self._hscroll(t, e), add="+")
            txt.bind("<Shift-Button-4>", lambda e, t=txt: self._hscroll(t, e, -1), add="+")
            txt.bind("<Shift-Button-5>", lambda e, t=txt: self._hscroll(t, e, 1), add="+")
            txt.tag_configure("highlight", background="#fff3b0")
            txt.tag_configure("comment", foreground="#0b8043")

            txt.bind("<Button-1>", lambda e, idx=i: self._set_pane(idx), add="+")
            txt.bind("<FocusIn>", lambda e, idx=i: self._set_pane(idx), add="+")
            txt.bind("<KeyRelease>", lambda e: self._schedule_comment_refresh(), add="+")
            txt.bind("<<Modified>>", self._on_text_modified, add="+")

            self.paned.add(frame, minsize=52, stretch="always")
            self.texts.append(txt)
            self.headers.append(label)
            self.bind_labels.append(bind_label)

        # ---- 右侧：发送 / 圆圈 / 设置 ------------------------------------
        right = tk.Frame(self, bg=BG)
        right.grid(row=0, column=2, rowspan=3, sticky="ns", padx=(2, 8), pady=(8, 8))
        right.rowconfigure(3, weight=1)

        self.btn_send = tk.Button(right, text="发送", font=self.font_btn, width=6, height=2,
                                  bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
                                  activeforeground="white", relief="raised", bd=1,
                                  command=self.on_send)
        self.btn_send.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.btn_send.bind("<Button-3>", self._on_send_menu)
        self.btn_send.bind("<Shift-Button-1>", lambda e: (self.on_send(whole=True), "break")[1])

        holder = tk.Frame(right, bg=BG)
        holder.grid(row=1, column=0, pady=(12, 0))
        self.circle = tk.Canvas(holder, width=58, height=58, bg=BG, highlightthickness=0,
                                cursor="hand2")
        self.circle.pack()
        self.circle.create_oval(9, 9, 49, 49, outline="#9aa0a6", width=5, tags="ring")
        self.circle.create_text(29, 29, text="0", fill=GREY, font=self.font_bold, tags="num")
        self.circle.bind("<ButtonPress-1>", self._on_circle_press)
        self.circle.bind("<B1-Motion>", self._on_circle_motion)
        self.circle.bind("<ButtonRelease-1>", self._on_circle_release)
        self.circle.bind("<Button-3>", self._on_uart_menu)
        self.circle.bind("<Double-Button-1>", lambda e: self._pick_from_list())
        tk.Label(right, text="拖到目标\n窗口松手", bg=BG, fg=GREY, font=self.font_small,
                 justify="center").grid(row=2, column=0, pady=(2, 0))

        self.btn_settings = tk.Button(right, text="设置", font=self.font, width=6,
                                      command=self.on_settings)
        self.btn_settings.grid(row=4, column=0, sticky="sew")

        self._update_circle_look()

    # ------------------------------------------------------------- 快捷键
    def _bind_shortcuts(self):
        self.bind_all("<Control-s>", lambda e: (self.on_save(), "break")[1])
        self.bind_all("<Control-S>", lambda e: (self.on_save_as(), "break")[1])
        self.bind_all("<Control-o>", lambda e: (self.on_open(), "break")[1])
        self.bind_all("<Control-O>", lambda e: (self.on_open(), "break")[1])
        self.bind_all("<Control-Return>", lambda e: (self.on_send(), "break")[1])
        self.bind_all("<Control-Shift-Return>",
                      lambda e: (self.on_send(whole=True), "break")[1])
        self.bind_all("<Control-a>", self._select_all)

    def _select_all(self, event):
        w = event.widget
        if isinstance(w, tk.Text):
            w.tag_add("sel", "1.0", "end-1c")
            return "break"
        if isinstance(w, tk.Entry):
            w.select_range(0, "end")
            return "break"
        return None

    # ------------------------------------------------------------- 状态栏
    def set_status(self, text):
        self.status_var.set(text)

    def _icon_candidates(self):
        """图标文件查找顺序：exe 同级 → onefile 解包目录 → 源码目录。"""
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs = [exe_dir]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
        names = ("app.ico", "app_icon.png") if os.name == "nt" else ("app_icon.png", "app.ico")
        seen, result = set(), []
        for folder in dirs:
            for name in names:
                path = os.path.join(folder, name)
                key = os.path.normcase(path)
                if key not in seen and os.path.exists(path):
                    seen.add(key)
                    result.append(path)
        return result

    def _set_window_icon(self):
        """设置窗口/任务栏图标。

        优先用图标文件；onefile 打包又没带 --add-data app.ico 时，直接从 exe 自身的
        RT_ICON 资源里取（--icon 已经把图标编译进去了），所以拷到哪里都不会丢。
        """
        for path in self._icon_candidates():
            try:
                if path.lower().endswith(".ico") and os.name == "nt":
                    self.iconbitmap(path)
                    return
                if path.lower().endswith(".png"):
                    self._icon_img = tk.PhotoImage(file=path)
                    self.iconphoto(True, self._icon_img)
                    return
            except Exception:
                continue
        if os.name == "nt":                      # 兜底：从 exe 资源里抠图标
            try:
                winutil.apply_exe_icon(winutil.root_hwnd(self.winfo_id()))
            except Exception:
                pass

    def _apply_opacity(self):
        try:
            value = float(self.cfg.get("opacity", 1.0))
        except (TypeError, ValueError):
            value = 1.0
        value = min(1.0, max(0.3, value))
        try:
            self.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _apply_topmost(self):
        """本窗口是否始终浮在其它窗口之上（即使目标窗口被切到前台）。"""
        on = bool(self.cfg.get("always_on_top"))
        try:
            self.attributes("-topmost", on)
        except tk.TclError:
            pass
        try:                                  # Windows 下再用 Win32 兜底一次
            winutil.set_topmost(winutil.root_hwnd(self.winfo_id()), on)
        except Exception:
            pass

    def toggle_topmost(self):
        self.cfg["always_on_top"] = not bool(self.cfg.get("always_on_top"))
        self._apply_topmost()
        save_config(self.cfg)
        self.set_status("窗口已置顶：切到目标窗口后本窗口仍浮在最上面"
                        if self.cfg["always_on_top"] else "已取消窗口置顶")

    def _update_badge(self):
        """顶部徽标：显示当前页对应的 uart 槽位及其绑定状态。"""
        if self.display_mode == MODE_SINGLE:
            bound_n = sum(1 for x in self.bind_titles if x)
            self.target_badge.configure(
                text=f"原样模式 · ##>n 自动切换（{bound_n}/{PANE_COUNT} 已绑定）",
                bg="#5f6368" if bound_n else "#9aa0a6")
            return
        idx = self.active_pane
        bound = bool(self.bind_titles[idx])
        self.target_badge.configure(
            text=f"目标：cmd{idx} → uart{idx}",
            bg=OK_GREEN if bound else "#9aa0a6")

    # ---------------------------------------------------------------- 文档
    def on_open(self):
        if not self._confirm_discard():
            return
        init = self._initial_dir()
        path = filedialog.askopenfilename(
            title="打开命令文件", initialdir=init,
            filetypes=[("文本文件", "*.txt *.cmd *.sh *.sc"), ("所有文件", "*.*")])
        if path:
            self.load_file(path)

    def _initial_dir(self):
        if self.file_path:
            return os.path.dirname(self.file_path)
        recent = self.cfg.get("recent_files") or []
        if recent:
            folder = os.path.dirname(recent[0])
            if os.path.isdir(folder):
                return folder
        return app_dir()

    # ---- 最近打开的文件（下拉框）----------------------------------------
    def _refresh_recent(self):
        recent = [p for p in self.cfg.get("recent_files", [])][:MAX_RECENT]
        self.cfg["recent_files"] = recent
        try:
            self.path_entry.configure(values=recent)
        except tk.TclError:
            pass

    def _remember_recent(self, path):
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        recent = [p for p in self.cfg.get("recent_files", []) if os.path.normcase(p) != key]
        recent.insert(0, path)
        self.cfg["recent_files"] = recent[:MAX_RECENT]
        self._refresh_recent()
        save_config(self.cfg)

    def _on_recent_selected(self, _event=None):
        path = self.path_var.get().strip().strip('"')
        if not path:
            return
        if not os.path.isfile(path):
            messagebox.showwarning("打开文件", "文件不存在（可能已被移动或删除）：\n" + path)
            return
        if self.file_path and os.path.normcase(os.path.abspath(path)) == \
                os.path.normcase(self.file_path):
            return
        if self._confirm_discard():
            self.load_file(path)

    def _on_recent_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        recent = self.cfg.get("recent_files", [])
        if recent:
            for p in recent[:MAX_RECENT]:
                mark = "✓ " if self.file_path and os.path.normcase(p) == os.path.normcase(self.file_path) else "  "
                menu.add_command(label=mark + p, command=lambda p=p: self._open_recent(p))
            menu.add_separator()
            menu.add_command(label="清空最近访问列表", command=self._clear_recent)
        else:
            menu.add_command(label="（还没有打开过文件）", state="disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _open_recent(self, path):
        if not os.path.isfile(path):
            messagebox.showwarning("打开文件", "文件不存在（可能已被移动或删除）：\n" + path)
            return
        if self._confirm_discard():
            self.load_file(path)

    def _clear_recent(self):
        self.cfg["recent_files"] = []
        self._refresh_recent()
        save_config(self.cfg)
        self.set_status("已清空最近访问列表")

    def _on_path_enter(self, _event=None):
        path = self.path_var.get().strip().strip('"')
        if path and os.path.isfile(path):
            if self._confirm_discard():
                self.load_file(path)
        elif path:
            messagebox.showwarning("打开文件", "文件不存在：\n" + path)

    def load_file(self, path):
        text = None
        enc_used = "utf-8"
        for enc in ("utf-8-sig", "utf-8", "gbk", "big5", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as fh:
                    text = fh.read()
                enc_used = enc
                break
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                messagebox.showerror("打开失败", str(exc))
                return
        if text is None:
            messagebox.showerror("打开失败", "无法识别文件编码。")
            return

        self._fill_document(text)

        self.file_path = os.path.abspath(path)
        self.encoding = enc_used
        self.dirty = False
        self.path_var.set(self.file_path)
        self._update_title()
        self._refresh_all_marks()
        self.refresh_comments()
        self._remember_recent(self.file_path)
        total = self._count_commands()
        self.set_status(f"已打开 {os.path.basename(path)} · 有效命令 {total} 行")
        self._set_pane(0)

    @staticmethod
    def parse_document(text):
        """按 ##>n 指令把内容切分到 3 个页签。"""
        panes = [[] for _ in range(PANE_COUNT)]
        cur = 0
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            m = DIR_RE.match(line)
            if m:
                idx = int(m.group(1))
                if 0 <= idx < PANE_COUNT:
                    cur = idx
                rest = m.group(2)
                if rest.strip() and not rest.strip().startswith("#"):
                    panes[cur].append(rest)
                continue
            panes[cur].append(line)
        # 去掉首尾的空行
        for i in range(PANE_COUNT):
            while panes[i] and not panes[i][0].strip():
                panes[i].pop(0)
            while panes[i] and not panes[i][-1].strip():
                panes[i].pop()
        return panes

    def pane_body(self, idx):
        body = self.texts[idx].get("1.0", "end-1c")
        return body

    def _fill_document(self, text):
        """把整份文件文本装进界面：拆分模式分栏，原样模式整段放 cmd0。"""
        if self.display_mode == MODE_SINGLE:
            body = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
            payloads = [body] + [""] * (PANE_COUNT - 1)
        else:
            payloads = ["\n".join(p) for p in self.parse_document(text)]
        for i, txt in enumerate(self.texts):
            txt.delete("1.0", "end")
            if payloads[i]:
                txt.insert("1.0", payloads[i])
            txt.edit_reset()
            txt.edit_modified(False)

    def _count_commands(self):
        n = 0
        for txt in self.texts:
            last = int(txt.index("end-1c").split(".")[0])
            for ln in range(1, last + 1):
                s = txt.get(f"{ln}.0", f"{ln}.end").strip()
                if s and not s.startswith("#"):
                    n += 1
        return n

    def serialize(self):
        if self.display_mode == MODE_SINGLE:
            body = self.pane_body(0).strip("\n")
            return (body + "\n") if body.strip() else ""
        bodies = [self.pane_body(i) for i in range(PANE_COUNT)]
        non_empty = [i for i in range(PANE_COUNT) if bodies[i].strip()]
        if not non_empty or non_empty == [0]:
            # 只有 cmd0 有内容时不写指令头，保持文件干净
            return bodies[0].rstrip("\n") + "\n" if bodies[0].strip() else ""
        out = []
        for i in non_empty:
            if out:
                out.append("")
            out.append(f"##>{i}")
            out.append(bodies[i].strip("\n"))
        return "\n".join(out) + "\n"

    def on_save(self):
        if not self.file_path:
            return self.on_save_as()
        return self._write(self.file_path)

    def on_save_as(self):
        init = os.path.dirname(self.file_path) if self.file_path else os.getcwd()
        name = os.path.basename(self.file_path) if self.file_path else "commands.txt"
        path = filedialog.asksaveasfilename(title="保存为", initialdir=init,
                                            initialfile=name, defaultextension=".txt",
                                            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return False
        return self._write(path)

    def _write(self, path):
        content = self.serialize()
        enc = self.encoding if self.encoding not in ("latin-1",) else "utf-8"
        try:
            with open(path, "w", encoding=enc, newline="\n") as fh:
                fh.write(content)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return False
        self.file_path = os.path.abspath(path)
        self.encoding = enc
        self.path_var.set(self.file_path)
        self.dirty = False
        self._update_title()
        self._remember_recent(self.file_path)
        self.set_status(f"已保存 · {os.path.basename(path)}")
        return True

    def _update_title(self):
        name = os.path.basename(self.file_path) if self.file_path else "未命名"
        star = " *" if self.dirty else ""
        self.title(f"{APP_TITLE} — {name}{star}")

    def mark_dirty(self, *_):
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _on_text_modified(self, event):
        w = event.widget
        if w.edit_modified():
            self.mark_dirty()
            w.edit_modified(False)

    def _confirm_discard(self):
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel("未保存", "当前内容有修改，是否保存？")
        if ans is None:
            return False
        if ans:
            return bool(self.on_save())
        return True

    # ------------------------------------------------------------- 页签切换
    def _set_pane(self, idx):
        if self.display_mode == MODE_SINGLE:
            idx = 0
        self.active_pane = idx
        for i, lbl in enumerate(self.headers):
            lbl.configure(fg=TXT if i == idx else GREY,
                          font=self.font_bold if i == idx else self.font)
        self._update_badge()
        self._update_circle_look()

    # --------------------------------------------------------- 显示模式
    def on_toggle_mode(self):
        target = MODE_SINGLE if self.display_mode == MODE_SPLIT else MODE_SPLIT
        name = "原样显示（不拆分）" if target == MODE_SINGLE else "拆分显示（cmd0/1/2）"
        if self.display_mode == target:
            return
        self._apply_display_mode(target, convert=True)
        self.cfg["display_mode"] = target
        save_config(self.cfg)
        self.set_status(f"已切换为{name}" +
                        ("：发送时遇到 ##>n 会自动换到对应 uart" if target == MODE_SINGLE
                         else "：三个 cmd 页分别对应 uart0 / uart1 / uart2"))
        if self._has_content():
            self.mark_dirty()

    def _has_content(self):
        return any(self.pane_body(i).strip() for i in range(PANE_COUNT))

    def _apply_display_mode(self, mode, convert=True):
        """切换显示模式；convert=True 时把当前内容按新格式重排（不丢内容）。"""
        old = self.display_mode
        if convert and old != mode:
            if mode == MODE_SINGLE:
                doc = self.serialize()          # 多栏 → 一份带 ##>n 的原文
                if not doc:
                    doc = "\n".join(self.pane_body(i) for i in range(PANE_COUNT))
            else:
                doc = self.pane_body(0)         # 原文 → 按 ##>n 拆栏
            self.display_mode = mode
            self._fill_document(doc)
        else:
            self.display_mode = mode

        single = mode == MODE_SINGLE
        for i, frame in enumerate(self.pane_frames):
            try:
                self.paned.paneconfigure(frame, hide=bool(single and i > 0))
            except tk.TclError:
                pass
        self.headers[0].configure(text="原文 · 不拆分显示" if single else "cmd 0")
        self.btn_mode.configure(text="原样" if single else "拆分")
        self.texts[0].focus_set()
        self.active_pane = 0 if single else self.active_pane
        self.refresh_comments()
        self._refresh_uart_list()
        self._update_badge()

    # --------------------------------------------------------- 注释 / 定位
    def _schedule_comment_refresh(self):
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except ValueError:
                pass
        self._refresh_job = self.after(350, self.refresh_comments)

    @staticmethod
    def _hscroll(txt, event, direction=None):
        if direction is None:
            step = -1 if getattr(event, "delta", 0) > 0 else 1
        else:
            step = direction
        try:
            txt.xview_scroll(step, "units")
        except tk.TclError:
            pass
        return "break"

    @staticmethod
    def _group_display(text):
        """#======测试1==== → 测试1（窄面板里看得清）。"""
        core = re.sub(r"^#+\s*=*", "", text.strip())
        core = re.sub(r"=+\s*$", "", core).strip()
        return core or text.strip()

    @staticmethod
    def _comment_label(pane, line, text, single):
        prefix = f"{line:>3}" if single else f"{pane}·{line:>2}"
        return f"{prefix}  {text}"

    def refresh_comments(self):
        """重建左侧注释树：#===xxx=== 作为可展开/收起的分组标题。"""
        self._refresh_job = None
        single = self.display_mode == MODE_SINGLE
        groups, loose, flat = [], [], []
        for i, txt in enumerate(self.texts):
            last = int(txt.index("end-1c").split(".")[0])
            cur = None
            for ln in range(1, last + 1):
                line = txt.get(f"{ln}.0", f"{ln}.end")
                s = line.strip()
                if s.startswith("#") and not DIR_RE.match(s):
                    txt.tag_add("comment", f"{ln}.0", f"{ln}.end")
                    flat.append((i, ln, s))
                    if GROUP_RE.match(s):
                        cur = {"pane": i, "line": ln, "text": s, "items": []}
                        groups.append(cur)
                    elif cur is not None:
                        cur["items"].append((i, ln, s))
                    else:
                        loose.append((i, ln, s))
                else:
                    txt.tag_remove("comment", f"{ln}.0", f"{ln}.end")
        self.comment_items = flat

        tree = self.comment_tree
        idx = {}
        tree.delete(*tree.get_children(""))
        for pane, ln, text in loose:
            iid = f"c{pane}_{ln}"
            tree.insert("", "end", iid=iid, tags=("item",),
                        text="  " + self._comment_label(pane, ln, text, single))
            idx[iid] = (pane, ln)
        for grp in groups:
            gid = f"g{grp['pane']}_{grp['line']}"
            count = len(grp["items"])
            label = self._group_display(grp["text"]) + (f"  ({count})" if count else "")
            tree.insert("", "end", iid=gid, tags=("group",), text=" " + label,
                        open=gid not in self._collapsed_groups)
            idx[gid] = (grp["pane"], grp["line"])
            for pane, ln, text in grp["items"]:
                cid = f"c{pane}_{ln}"
                tree.insert(gid, "end", iid=cid, tags=("item",),
                            text="   " + self._comment_label(pane, ln, text, single))
                idx[cid] = (pane, ln)
        self._comment_index = idx

    def _remember_open_state(self, _event=None):
        """记下哪些分组被收起来了，重建列表时保持状态。"""
        tree = self.comment_tree
        try:
            self._collapsed_groups = {
                iid for iid in tree.get_children("")
                if tree.get_children(iid) and not tree.item(iid, "open")
            }
        except tk.TclError:
            pass

    def _refresh_all_marks(self):
        for txt in self.texts:
            txt.tag_remove("comment", "1.0", "end")

    def _on_comment_double(self, event=None):
        tree = self.comment_tree
        if event is not None:
            iid = tree.identify_row(event.y)
        else:
            iid = tree.focus()
        if not iid:
            return "break"
        has_children = bool(tree.get_children(iid))
        if has_children:
            bbox = tree.bbox(iid)
            # 点在文字上 → 展开/收起；点在小三角上交给 Treeview 自己的默认行为
            if not bbox or event is None or event.x >= bbox[0]:
                tree.item(iid, open=not bool(tree.item(iid, "open")))
                self._remember_open_state()
            return "break"
        pane, ln = self._comment_index.get(iid, (None, None))
        if pane is not None:
            self.jump_to(pane, ln)
        return "break"

    def jump_to(self, pane, lineno):
        self._set_pane(pane)
        txt = self.texts[pane]
        txt.focus_set()
        txt.tag_remove("highlight", "1.0", "end")
        txt.tag_add("highlight", f"{lineno}.0", f"{lineno}.end")
        txt.mark_set("insert", f"{lineno}.0")
        txt.see(f"{lineno}.0")
        self.after(1800, lambda: txt.tag_remove("highlight", "1.0", "end"))
        where = f"第 {lineno} 行" if self.display_mode == MODE_SINGLE else f"cmd{pane} 第 {lineno} 行"
        self.set_status(f"已定位到 {where}")

    # ---------------------------------------------------------- uart 绑定
    def _refresh_uart_list(self):
        """左侧绑定列表：只显示通道号 0~4，把位置让给窗口标题。"""
        self._updating_list = True
        self.uart_list.delete(0, "end")
        for i in range(PANE_COUNT):
            title = self.bind_titles[i]
            if title:
                self.uart_list.insert("end", f"{i}  ● {title}")
                self.uart_list.itemconfig(i, foreground=OK_GREEN)
            else:
                self.uart_list.insert("end", f"{i}  ○ 未绑定")
                self.uart_list.itemconfig(i, foreground=GREY)
        if self.slot < self.uart_list.size():
            self.uart_list.selection_clear(0, "end")
            self.uart_list.selection_set(self.slot)
        self._updating_list = False
        for i in range(PANE_COUNT):
            t = self.bind_titles[i]
            if self.display_mode == MODE_SINGLE:
                if i == 0:
                    bound_n = sum(1 for x in self.bind_titles if x)
                    text = f"→ 按 ##>n 自动切换（已绑定 {bound_n}/{PANE_COUNT}）"
                else:
                    text = ""
                color = GREY
            elif t:
                text = f"→ uart{i}: {t[:28]}"
                color = OK_GREEN
            else:
                text = f"→ uart{i}: 未绑定窗口"
                color = GREY
            self.bind_labels[i].configure(text=text, fg=color)
        self._update_badge()
        self._update_circle_look()

    def _update_circle_look(self):
        bound = bool(self.bind_titles[self.slot])
        idx = self.slot
        color = OK_GREEN if bound else "#9aa0a6"
        if self.sending:
            color = WARN
        self.circle.itemconfigure("ring", outline=color)
        self.circle.itemconfigure("num", text=str(idx), fill=color)

    def _on_uart_select(self, _event=None):
        if self._updating_list:
            return
        sel = self.uart_list.curselection()
        if not sel:
            return
        self.slot = sel[0]
        if self.display_mode == MODE_SPLIT:
            self._set_pane(self.slot)
        else:
            self._update_circle_look()
        title = self.bind_titles[self.slot] or "未绑定"
        self.set_status(f"已选中 uart{self.slot} · {title} ｜ 把圆圈拖到目标窗口即可绑定")

    def _on_uart_double(self, event=None):
        self._on_uart_select()
        if event is not None:
            try:
                index = self.uart_list.nearest(event.y)
                self.uart_list.selection_clear(0, "end")
                self.uart_list.selection_set(index)
                self.slot = index
                self._set_pane(index)
            except tk.TclError:
                pass
        self.set_status(f"uart{self.slot} 已选中 · 按住右侧圆圈拖到目标窗口")

    def _on_uart_menu(self, event):
        # 右键：选择第 n 行（若点在列表上）或直接针对当前槽位
        if event is not None and event.widget is self.uart_list:
            try:
                self.slot = self.uart_list.nearest(event.y)
                self.uart_list.selection_clear(0, "end")
                self.uart_list.selection_set(self.slot)
                self._set_pane(self.slot)
            except tk.TclError:
                pass
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"从窗口列表选择（绑定 uart{self.slot}）",
                         command=self._pick_from_list)
        menu.add_command(label=f"清除 uart{self.slot} 绑定", command=self._clear_binding)
        menu.add_separator()
        for i in range(PANE_COUNT):
            state = "normal" if self.bind_titles[i] else "disabled"
            label = f"uart{i}: {self.bind_titles[i][:30]}" if self.bind_titles[i] else f"uart{i}: 未绑定"
            menu.add_command(label=label, state=state,
                             command=lambda i=i: self.jump_to(i, 1))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _clear_binding(self):
        self.bindings[self.slot] = None
        self.bind_titles[self.slot] = ""
        self._refresh_uart_list()
        self.set_status(f"已清除 uart{self.slot} 的窗口绑定")

    def _bind_window(self, info):
        if not info:
            return
        self.bindings[self.slot] = info["hwnd"]
        title = info["title"].strip() or (info.get("exe") or "未命名窗口")
        self.bind_titles[self.slot] = title
        self._refresh_uart_list()
        self.set_status(f"uart{self.slot} 已绑定窗口：{title}")

    def _pick_from_list(self):
        wins = winutil.list_windows()
        if not wins:
            messagebox.showinfo("选择窗口", "没有找到可用的窗口（或当前不是 Windows 系统）。")
            return
        dlg = tk.Toplevel(self)
        dlg.title(f"选择 uart{self.slot} 的目标窗口")
        dlg.transient(self)
        dlg.configure(bg=BG)
        dlg.geometry("560x360")
        tk.Label(dlg, text="当前所有可见窗口（双击绑定）", bg=BG, fg=TXT,
                 font=self.font).pack(anchor="w", padx=10, pady=(10, 4))
        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        lb = tk.Listbox(frame, font=self.font, exportselection=False, activestyle="none")
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.configure(yscrollcommand=sb.set)
        for w in wins:
            lb.insert("end", f"{w['title']}   [{w.get('exe') or w['pid']}]")

        def choose(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            self._bind_window(wins[sel[0]])
            dlg.destroy()

        lb.bind("<Double-Button-1>", choose)
        lb.bind("<Return>", choose)
        lb.focus_set()
        self._center_on(dlg)

    def _center_on(self, win):
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------ 圆圈拖拽
    def _on_circle_press(self, event):
        self._drag_active = True
        self._drag_moved = False
        self.circle.configure(cursor="crosshair")
        self.circle.itemconfigure("ring", outline=WARN, width=6)
        self._show_overlay(event.x_root, event.y_root)
        self.set_status(f"正在为 uart{self.slot} 拾取窗口：把鼠标移到目标窗口上松开")

    def _on_circle_motion(self, event):
        if not getattr(self, "_drag_active", False):
            return
        self._drag_moved = True
        x, y = self.winfo_pointerxy()
        self._move_overlay(x, y)
        info = winutil.window_from_point(x, y, exclude_pid=os.getpid())
        if info:
            self._overlay_text(f"✔ {info['title'][:38]}")
        else:
            self._overlay_text("拖到目标窗口后松开")

    def _on_circle_release(self, event):
        if not getattr(self, "_drag_active", False):
            return
        self._drag_active = False
        self.circle.configure(cursor="hand2")
        x, y = self.winfo_pointerxy()
        self._hide_overlay()
        self._update_circle_look()
        if not getattr(self, "_drag_moved", False):
            # 单击圆圈 = 选中当前槽位，提示用法
            self._set_pane(self.slot)
            self.set_status(f"uart{self.slot}：" +
                            (self.bind_titles[self.slot] or "未绑定") +
                            " ｜ 按住圆圈拖到目标窗口即可绑定")
            return
        info = winutil.window_from_point(x, y, exclude_pid=os.getpid())
        if not info:
            self.set_status("没有拾取到窗口（松开时鼠标需停在目标窗口上）")
            return
        self._bind_window(info)

    def _show_overlay(self, x, y):
        self._hide_overlay()
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        try:
            top.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        top.configure(bg="#111111")
        lbl = tk.Label(top, text="拖到目标窗口后松开", bg="#111111", fg="#ffffff",
                       font=self.font_small, padx=8, pady=4)
        lbl.pack()
        self._drag_overlay = top
        self._overlay_label = lbl
        self._move_overlay(x, y)

    def _move_overlay(self, x, y):
        if self._drag_overlay is None:
            return
        try:
            self._drag_overlay.geometry(f"+{x + 18}+{y + 18}")
        except tk.TclError:
            pass

    def _overlay_text(self, text):
        if self._drag_overlay is None:
            return
        try:
            self._overlay_label.configure(text=text)
        except tk.TclError:
            pass

    def _hide_overlay(self):
        if self._drag_overlay is not None:
            try:
                self._drag_overlay.withdraw()
                self._drag_overlay.destroy()
            except tk.TclError:
                pass
            self._drag_overlay = None
            self.update_idletasks()

    # ---------------------------------------------------------------- 发送
    def _on_send_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="发送选中行 / 光标所在行（默认）",
                         command=lambda: self.on_send())
        scope = "整个文本" if self.display_mode == MODE_SINGLE else f"整个 cmd{self.active_pane}"
        menu.add_command(label=f"发送{scope}", command=lambda: self.on_send(whole=True))
        menu.add_separator()
        var = tk.BooleanVar(value=bool(self.cfg.get("always_on_top")))
        menu.add_checkbutton(label="窗口始终置顶", variable=var, command=self.toggle_topmost)
        menu.add_command(label="设置…", command=self.on_settings)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _range_lines(self, txt, whole=False):
        """返回 (起始行, 结束行)，1 起；无选中时取光标所在行。"""
        if whole:
            start, end = txt.index("1.0"), txt.index("end-1c")
        else:
            try:
                start, end = txt.index("sel.first"), txt.index("sel.last")
            except tk.TclError:
                ln = int(txt.index("insert").split(".")[0])
                start, end = f"{ln}.0", f"{ln}.end"
        first = int(start.split(".")[0])
        last = int(end.split(".")[0])
        if end.split(".")[1] == "0" and last > first:
            last -= 1
        return first, last

    def gather_lines(self, pane, whole=False):
        """取出需要发送的行：有选中就发选中的，否则只发光标所在行。"""
        txt = self.texts[pane]
        first, last = self._range_lines(txt, whole)
        lines, skipped = [], 0
        for ln in range(first, last + 1):
            raw = txt.get(f"{ln}.0", f"{ln}.end")
            s = raw.strip()
            if not s:
                if self.cfg.get("skip_empty", True):
                    skipped += 1
                    continue
                lines.append("")
                continue
            if s.startswith("#"):
                skipped += 1
                continue
            lines.append(raw.rstrip())
        return lines, skipped

    @staticmethod
    def _is_sendable_line(txt, ln):
        line = txt.get(f"{ln}.0", f"{ln}.end").strip()
        return bool(line) and not line.startswith("#")

    def _ensure_sendable_cursor(self):
        """光标停在注释/空行/##>n 上时，先跳到下一条可发送的行。

        有选中内容时不动（尊重用户的选择）。
        """
        pane = 0 if self.display_mode == MODE_SINGLE else self.active_pane
        txt = self.texts[pane]
        try:
            txt.index("sel.first")
            return False
        except tk.TclError:
            pass
        ln = int(txt.index("insert").split(".")[0])
        if self._is_sendable_line(txt, ln):
            return False
        total = int(txt.index("end-1c").split(".")[0])
        nxt = ln + 1
        while nxt <= total and not self._is_sendable_line(txt, nxt):
            nxt += 1
        if nxt > total:
            return False
        txt.tag_add("sel", f"{nxt}.0", f"{nxt}.end")
        txt.mark_set("insert", f"{nxt}.0")
        txt.see(f"{nxt}.0")
        return True

    def _advance_to_next_line(self):
        """发送结束后把光标/选区移到下一条真正能发的行（跳过空行、注释、##>n）。"""
        pane = 0 if self.display_mode == MODE_SINGLE else self.active_pane
        txt = self.texts[pane]
        _first, last = self._range_lines(txt, False)
        total = int(txt.index("end-1c").split(".")[0])
        nxt = last + 1
        while nxt <= total and not self._is_sendable_line(txt, nxt):
            nxt += 1
        if nxt > total:                       # 已经到最后，保持原样
            return False
        txt.tag_remove("sel", "1.0", "end")
        txt.tag_add("sel", f"{nxt}.0", f"{nxt}.end")
        txt.mark_set("insert", f"{nxt}.0")
        txt.see(f"{nxt}.0")
        return True

    def _build_plan(self, whole=False):
        """把待发送内容整理成 [(uart序号, [行...]), ...]，一行一条通路。"""
        if self.display_mode == MODE_SPLIT:
            lines, skipped = self.gather_lines(self.active_pane, whole)
            if not lines:
                return [], skipped
            return [(self.active_pane, lines)], skipped

        # 原样模式：顺序扫描，遇到 ##>n 就切换目标
        txt = self.texts[0]
        first, last = self._range_lines(txt, whole)
        cur = 0
        for ln in range(1, first):          # 区间之前生效的指令
            m = DIR_RE.match(txt.get(f"{ln}.0", f"{ln}.end"))
            if m:
                idx = int(m.group(1))
                if 0 <= idx < PANE_COUNT:
                    cur = idx

        groups, skipped = [], 0
        for ln in range(first, last + 1):
            raw = txt.get(f"{ln}.0", f"{ln}.end")
            s = raw.strip()
            m = DIR_RE.match(raw)
            if m:
                idx = int(m.group(1))
                if 0 <= idx < PANE_COUNT:
                    cur = idx
                skipped += 1
                continue
            if not s:
                if self.cfg.get("skip_empty", True):
                    skipped += 1
                    continue
                groups.append((cur, [""]))
                continue
            if s.startswith("#"):
                skipped += 1
                continue
            groups.append((cur, [raw.rstrip()]))

        # 合并相邻的同目标行，避免频繁激活窗口
        plan = []
        for uart, lines in groups:
            if plan and plan[-1][0] == uart:
                plan[-1][1].extend(lines)
            else:
                plan.append((uart, list(lines)))
        return plan, skipped

    def _missing_targets(self, plan):
        missing = []
        for uart, _lines in plan:
            hwnd = self.bindings[uart]
            if not hwnd or not winutil.is_window(hwnd):
                if hwnd:                     # 窗口已关闭，清掉绑定
                    self.bindings[uart] = None
                    self.bind_titles[uart] = ""
                if uart not in missing:
                    missing.append(uart)
        if missing:
            self._refresh_uart_list()
        return missing

    def on_send(self, whole=False):
        if self.sending:
            self.stop_flag.set()
            self.set_status("正在停止…")
            return
        if not whole:
            self._ensure_sendable_cursor()
        plan, skipped = self._build_plan(whole)
        if not plan:
            self.set_status("没有可发送的内容（注释行、##>n 指令行和空行会被忽略）")
            return
        missing = self._missing_targets(plan)
        if missing:
            names = "、".join(f"uart{u}（cmd{u}）" for u in missing)
            messagebox.showwarning(
                "没有目标窗口",
                f"以下目标还没有绑定窗口：{names}\n\n"
                "把右侧的圆圈拖到要输入命令的窗口上松手即可绑定。")
            return

        total = sum(len(lines) for _u, lines in plan)
        self.sending = True
        self.stop_flag.clear()
        self.btn_send.configure(text="停止", bg="#d93025", activebackground="#b3261e")
        self._update_circle_look()
        focus_mode = self.cfg.get("focus_mode", FOCUS_TEMP)
        if (focus_mode == FOCUS_NONE and self.cfg.get("send_method") != "input"
                and not winutil.is_foreground(self.bindings[plan[0][0]])):
            self.set_status("开始发送…（纯后台消息：若目标窗口没有反应，请在设置里改用“临时切换”）")
        elif len(plan) == 1:
            self.set_status(f"开始发送 {total} 行到 uart{plan[0][0]}…")
        else:
            targets = "→".join(f"uart{u}" for u, _ in plan)
            self.set_status(f"开始发送 {total} 行，按 {targets} 自动切换…")
        try:
            self_hwnd = winutil.root_hwnd(self.winfo_id())
        except tk.TclError:
            self_hwnd = None
        threading.Thread(target=self._send_worker, args=(plan, self_hwnd), daemon=True).start()

        # 发完这一行/这一段后，把选区挪到下一行，方便一路点下去
        if not whole and self.cfg.get("auto_advance", True):
            self._advance_to_next_line()

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_flag.is_set():
                return False
            time.sleep(min(0.02, max(0.001, end - time.time())))
        return True

    def _send_worker(self, plan, self_hwnd=None):
        cfg = self.cfg
        char_d = max(0, cfg["char_interval"]) / 1000.0
        line_d = max(0, cfg["line_interval"]) / 1000.0
        nl = NEWLINE_MAP.get(cfg["newline"], "\r\n")
        method = cfg["send_method"]
        compat = bool(cfg.get("keydown_compat"))
        focus_mode = cfg.get("focus_mode", FOCUS_TEMP)
        # 前台键盘模拟必须有焦点，不切换时也临时切一下
        must_focus = method == "input"
        restore_after = focus_mode == FOCUS_TEMP or (must_focus and focus_mode == FOCUS_NONE)
        total = sum(len(lines) for _u, lines in plan)
        done = 0
        ok = True
        switched = False
        try:
            for uart, lines in plan:
                hwnd = self.bindings[uart]
                if not hwnd:
                    ok = False
                    break
                # ---- 焦点处理 ----
                if (must_focus or focus_mode != FOCUS_NONE) and not winutil.is_foreground(hwnd):
                    self.msgq.put(("status", f"切换到 uart{uart} 的窗口…"))
                    if winutil.activate_window(hwnd):
                        switched = True
                        time.sleep(0.12)
                    else:
                        self.msgq.put(("status", "无法切换到目标窗口，尝试后台发送…"))
                for line in lines:
                    if self.stop_flag.is_set():
                        ok = False
                        break
                    done += 1
                    head = f"[uart{uart}] " if len(plan) > 1 else ""
                    self.msgq.put(("status", f"发送中 {done}/{total} · {head}{line[:48]}"))
                    text = line + nl
                    if method == "input":
                        ok = winutil.type_text_input(hwnd, text, char_d, self.stop_flag)
                    else:
                        ok = winutil.post_text(hwnd, text, char_d, self.stop_flag,
                                               keydown_compat=compat)
                    if not ok:
                        break
                    if done < total and line_d and not self._interruptible_sleep(line_d):
                        ok = False
                        break
                if not ok:
                    break
        except Exception as exc:  # 防止线程静默死掉
            self.msgq.put(("status", f"发送异常：{exc}"))
            ok = False
        finally:
            # 临时切换模式：发完把焦点还给本程序
            if restore_after and switched and self_hwnd:
                self.msgq.put(("status", "发送结束，切回本程序…"))
                winutil.activate_window(self_hwnd)
        targets = "、".join(f"uart{u}" for u, _ in plan)
        self.msgq.put(("done", (ok, total, targets)))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._send_finished(*payload)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _send_finished(self, ok, total, targets):
        self.sending = False
        self.btn_send.configure(text="发送", bg=ACCENT, activebackground=ACCENT_DARK)
        self._update_circle_look()
        if ok:
            self.set_status(f"发送完成 · {total} 行 → {targets}")
        else:
            self.set_status(f"发送中止 · {targets} 已完成 {total} 行中的一部分")

    # ---------------------------------------------------------------- 设置
    def on_settings(self):
        old_mode = self.display_mode
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if not dlg.result:                 # 取消 → 还原实时预览改过的透明度
            self._apply_opacity()
            return
        if dlg.result:
            self.cfg.update(dlg.result)
            new_mode = self.cfg.get("display_mode", old_mode)
            if new_mode != old_mode:
                self._apply_display_mode(new_mode, convert=True)
                if self._has_content():
                    self.mark_dirty()
            self._apply_topmost()
            self._apply_opacity()
            save_config(self.cfg)
            mode_txt = "拆分显示" if new_mode == MODE_SPLIT else "原样显示"
            focus_txt = {FOCUS_NONE: "不切换焦点", FOCUS_TEMP: "临时切前台",
                         FOCUS_KEEP: "保持前台"}.get(self.cfg.get("focus_mode"), "")
            top_txt = " · 窗口置顶" if self.cfg.get("always_on_top") else ""
            self.set_status(
                f"设置已保存 · {mode_txt} · {focus_txt}{top_txt} · "
                f"行间隔 {self.cfg['line_interval']}ms · 字符间隔 {self.cfg['char_interval']}ms")

    # ---------------------------------------------------------------- 退出
    def on_close(self):
        if self.sending:
            self.stop_flag.set()
            time.sleep(0.05)
        if not self._confirm_discard():
            return
        save_config(self.cfg)
        self.destroy()


def main():
    winutil.enable_dpi_awareness()
    winutil.set_app_user_model_id()      # 任务栏按本程序独立分组/显示图标
    app = App()
    # 支持命令行带文件启动：UartSender.exe commands.txt
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.load_file(sys.argv[1])
    app.mainloop()


if __name__ == "__main__":
    main()
