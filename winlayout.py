# -*- coding: utf-8 -*-
"""
WinLayout — 窗口位置记忆器

把窗口摆好 → 勾选它们 → 存成一个方案 → 以后一键整套摆回。

只用标准库，无需 pip install 任何东西。
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time

# ================================================================ DPI 感知
# 必须在建窗口前调用，否则 Win11 高缩放（125%/150%）下坐标整体偏移。
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ctypes 默认把返回值当 c_int，64 位下会把指针截断，必须显式声明。
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetParent.restype = wt.HWND
user32.MonitorFromWindow.restype = wt.HANDLE
user32.IsZoomed.restype = wt.BOOL
user32.IsIconic.restype = wt.BOOL
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]

SW_MAXIMIZE = 3
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB201

CHECKED, UNCHECKED = "☑", "☐"

SKIP_CLASSES = {
    "Progman", "Shell_TrayWnd", "WorkerW", "Windows.UI.Core.CoreWindow",
    "ApplicationFrameWindow", "Button", "SysShadow", "ForegroundStaging",
    "Windows.UI.Composition.DesktopWindowContentBridge",
    "Xaml_WindowedPopupClass", "NotifyIconOverflowWindow", "TaskListThumbnailWnd",
    "tooltips_class32", "TopLevelWindowForOverflowXamlIsland",
}


# ================================================================ 窗口枚举
def _full_path(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return ""


def proc_of(pid):
    return os.path.basename(_full_path(pid))


def get_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value.strip()


def list_windows():
    """所有可管理的顶层窗口。"""
    out = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetParent(hwnd):        # 只要顶层窗口
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value in SKIP_CLASSES:
                return True
            title = get_text(hwnd)
            if not title:
                return True
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = int(pid.value)
            path = _full_path(pid)
            rc = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            out.append({
                "hwnd": int(hwnd), "title": title, "pid": pid,
                "proc": os.path.basename(path), "exe": path,
                "x": rc.left, "y": rc.top,
                "w": rc.right - rc.left, "h": rc.bottom - rc.top,
                "maximized": bool(user32.IsZoomed(hwnd)),
                "minimized": bool(user32.IsIconic(hwnd)),
            })
        except Exception:
            pass
        return True

    user32.EnumWindows(PROC(cb), 0)
    return out


class MONINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]


def list_monitors():
    """[(设备名, ux, uy, uw, uh, 是否主屏)]"""
    res = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HANDLE,
                             ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hm, _hdc, _rc, _d):
        try:
            mi = MONINFO()
            mi.cbSize = ctypes.sizeof(MONINFO)
            if user32.GetMonitorInfoW(hm, ctypes.byref(mi)):
                res.append((mi.szDevice, mi.rcWork.left, mi.rcWork.top,
                            mi.rcWork.right - mi.rcWork.left,
                            mi.rcWork.bottom - mi.rcWork.top,
                            bool(mi.dwFlags & 1)))
        except Exception:
            pass
        return True

    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return res


def monitor_of(hwnd):
    """窗口所在显示器的 (设备名, ux, uy, uw, uh, 主屏?)。"""
    hm = int(user32.MonitorFromWindow(wt.HWND(hwnd), MONITOR_DEFAULTTONEAREST))
    mi = MONINFO()
    mi.cbSize = ctypes.sizeof(MONINFO)
    mons = list_monitors()
    if user32.GetMonitorInfoW(wt.HANDLE(hm), ctypes.byref(mi)):
        for m in mons:
            if m[0] == mi.szDevice:
                return m
    return mons[0] if mons else ("", 0, 0, 1920, 1040, True)


# ================================================================ 记忆
def snapshot(win):
    """把窗口当前状态存成一条记忆。"""
    dev, ux, uy, uw, uh, _ = monitor_of(win["hwnd"])
    return {
        "proc": win["proc"],
        "title": win["title"],
        "exe": win["exe"],
        "x": win["x"], "y": win["y"], "w": win["w"], "h": win["h"],
        "maximized": win["maximized"],
        "monitor": dev,
        "ux": ux, "uy": uy, "uw": uw, "uh": uh,
    }


def target_rect(item):
    """算出现在应该摆到哪。

    · 屏幕还在、尺寸也没变 → 像素级精确还原
    · 换屏或分辨率变了 → 保持窗口相对那块屏的位置比例，换算到新屏
    · 记录时那块屏已不存在 → 落到主屏并按比例换算，保证不出屏
    """
    want = item.get("monitor", "")
    mons = list_monitors()
    if not mons:
        return None
    cur = next((m for m in mons if m[0] == want), None)
    same = cur is not None
    if cur is None:
        cur = next((m for m in mons if m[5]), mons[0])
    _, ux, uy, uw, uh, _ = cur

    ow, oh = item.get("uw") or uw, item.get("uh") or uh
    if same and abs(uw - ow) <= 2 and abs(uh - oh) <= 2:
        w = min(item["w"], uw)
        h = min(item["h"], uh)
        x = max(ux, min(item["x"], ux + uw - w))
        y = max(uy, min(item["y"], uy + uh - h))
        return x, y, w, h

    sx, sy = uw / max(1, ow), uh / max(1, oh)
    w = max(1, min(uw, int(round(item["w"] * sx))))
    h = max(1, min(uh, int(round(item["h"] * sy))))
    x = int(round(ux + (item["x"] - item.get("ux", ux)) * sx))
    y = int(round(uy + (item["y"] - item.get("uy", uy)) * sy))
    x = max(ux, min(x, ux + uw - w))
    y = max(uy, min(y, uy + uh - h))
    return x, y, w, h


def restore(items, autolaunch=False, log=lambda s: None):
    """把方案里的窗口全部摆回去。返回 (恢复数, 未运行列表)。

    按进程名分组后，组内每条记录依次占用该程序的一个窗口，
    这样即使配置里混进了同程序的多条记录，也不会全都作用到同一个窗口上。
    """
    wins = list_windows()
    # proc(小写) -> 该程序当前所有窗口，按枚举顺序
    by_proc = {}
    for w in wins:
        by_proc.setdefault(w["proc"].lower(), []).append(w)

    if autolaunch:
        running = set(by_proc)
        for it in items:
            p = (it.get("proc") or "").lower()
            if p and p not in running and it.get("exe") and os.path.exists(it["exe"]):
                log(f"启动 {it['proc']} …")
                try:
                    subprocess.Popen([it["exe"]], shell=False)
                    running.add(p)
                    time.sleep(0.8)
                    by_proc.setdefault(p, [])
                except Exception as e:
                    log(f"  启动失败：{e}")
        time.sleep(1.0)
        wins = list_windows()
        by_proc = {}
        for w in wins:
            by_proc.setdefault(w["proc"].lower(), []).append(w)

    used = {}          # proc -> 已经用掉几个窗口
    ok, miss = 0, []
    for it in items:
        proc = (it.get("proc") or "").lower()
        pool = by_proc.get(proc) or []
        n = used.get(proc, 0)
        if n >= len(pool):
            miss.append(it)
            continue
        cand = pool[n]
        used[proc] = n + 1

        if cand["minimized"]:
            user32.ShowWindow(cand["hwnd"], SW_RESTORE)
            time.sleep(0.15)
        if it.get("maximized"):
            user32.ShowWindow(cand["hwnd"], SW_MAXIMIZE)
            ok += 1
            log(f"  {it['proc']:<20} 最大化")
            continue
        r = target_rect(it)
        if not r:
            miss.append(it)
            continue
        x, y, w, h = r
        user32.SetWindowPos(wt.HWND(cand["hwnd"]), wt.HWND(HWND_TOP),
                            x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        ok += 1
        log(f"  {it['proc']:<20} → ({x},{y}) {w}x{h}")
    return ok, miss


# ================================================================ 配置
def config_path():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WinLayout")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "schemes.json")


def load_data():
    p = config_path()
    d = {"schemes": [], "hotkey": "Ctrl+Alt+R", "last": ""}
    if not os.path.exists(p):
        return d
    try:
        with open(p, "r", encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:
        pass
    if not isinstance(d.get("schemes"), list):
        d["schemes"] = []
    return d


def save_data(schemes, hotkey, last):
    p = config_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"schemes": schemes, "hotkey": hotkey, "last": last},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


# ================================================================ 热键
VK = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
VK.update({str(i): 0x30 + i for i in range(10)})
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})


def parse_hotkey(s):
    mods, vk = 0, None
    for part in [x.strip().upper() for x in str(s).replace("-", "+").split("+") if x.strip()]:
        if part in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif part == "ALT":
            mods |= MOD_ALT
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part in ("WIN", "META"):
            mods |= MOD_WIN
        elif part in VK:
            vk = VK[part]
    return (mods, vk) if vk is not None else None


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]


class Hotkey(threading.Thread):
    def __init__(self, combo, cb):
        super().__init__(daemon=True)
        self.combo, self.cb = combo, cb

    def run(self):
        msg = _MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        p = parse_hotkey(self.combo)
        if not p:
            return
        if not user32.RegisterHotKey(None, HOTKEY_ID, p[0], p[1]):
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.cb()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


# ================================================================ 界面
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog

    data = load_data()
    schemes = data["schemes"]
    hotkey = data["hotkey"]
    picked = {}          # proc -> window dict（勾选的窗口，同一程序只保留一个）
    WINDOWS = {}         # 列表行 id -> window dict
    _syncing = [False]     # 重建列表时抑制 select 事件，防递归
    log_lines = []

    root = tk.Tk()
    root.title("WinLayout — 窗口位置记忆器")
    root.geometry("720x620")
    root.minsize(660, 560)
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    base_font = ("Microsoft YaHei UI", 9)
    ttk.Style().configure(".", font=base_font)
    ttk.Style().configure("Big.TButton", font=("Microsoft YaHei UI", 11, "bold"))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    # ---------------------------------------------------------------- 顶部
    top = ttk.Frame(root, padding=(12, 10, 12, 6))
    top.grid(row=0, column=0, sticky="ew")
    ttk.Label(top, text="① 把窗口摆好 → 勾选 → 存成方案",
              font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
    ttk.Button(top, text="🔄 刷新窗口", width=10,
               command=lambda: refresh(True)).pack(side="right")

    body = ttk.Frame(root, padding=(12, 0, 12, 0))
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=3, minsize=330)
    body.columnconfigure(1, weight=2, minsize=250)

    # ============================ 左：勾选窗口
    left = ttk.LabelFrame(body, text="当前窗口（点最左方框勾选）", padding=6)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    left.columnconfigure(0, weight=1)
    left.rowconfigure(0, weight=1)

    tree = ttk.Treeview(left, columns=("chk", "proc", "title"), show="headings")
    tree.heading("chk", text="")
    tree.column("chk", width=34, stretch=False, anchor="center")
    tree.heading("proc", text="程序")
    tree.column("proc", width=120, stretch=False)
    tree.heading("title", text="窗口标题")
    tree.column("title", width=180, stretch=True)
    tree.grid(row=0, column=0, sticky="nsew")
    tsb = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tsb.set)
    tsb.grid(row=0, column=1, sticky="ns")

    lbtn = ttk.Frame(body)
    lbtn.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(8, 0))
    lbtn.columnconfigure(0, weight=1)
    sel_count = tk.StringVar(value="已勾选 0 个")
    ttk.Label(lbtn, textvariable=sel_count).grid(row=0, column=0, sticky="w")
    ttk.Button(lbtn, text="全不选", width=8,
               command=lambda: set_all(False)).grid(row=0, column=1, padx=2)

    # ============================ 右：方案
    right = ttk.Frame(body)
    right.grid(row=0, column=1, sticky="nsew")
    right.columnconfigure(0, weight=1)
    right.rowconfigure(1, weight=1)

    sb = ttk.LabelFrame(right, text="② 我的方案", padding=6)
    sb.grid(row=0, column=0, sticky="nsew")
    sb.columnconfigure(0, weight=1)
    sb.rowconfigure(0, weight=1)
    stree = ttk.Treeview(sb, columns=("cnt",), show="headings", height=8)
    stree.heading("cnt", text="窗口数")
    stree.column("cnt", width=66, stretch=False, anchor="center")
    stree.grid(row=0, column=0, sticky="ew")
    ssb = ttk.Scrollbar(sb, orient="vertical", command=stree.yview)
    stree.configure(yscrollcommand=ssb.set)
    ssb.grid(row=0, column=1, sticky="ns")

    sbtn = ttk.Frame(right)
    sbtn.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    sbtn.columnconfigure(0, weight=1)
    ttk.Button(sbtn, text="💾 存入为新方案", style="Big.TButton",
               command=lambda: save_new()).grid(row=0, column=0, sticky="ew")
    r1 = ttk.Frame(sbtn)
    r1.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    ttk.Button(r1, text="↩ 覆盖选中方案", width=15,
               command=lambda: overwrite()).pack(side="left")
    ttk.Button(r1, text="重命名", width=9,
               command=lambda: rename()).pack(side="left", padx=5)
    ttk.Button(r1, text="🗑 删除", width=9,
               command=lambda: delete()).pack(side="left")

    # ---- 恢复区
    rst = ttk.LabelFrame(root, text="③ 恢复", padding=8)
    rst.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 6))
    rst.columnconfigure(0, weight=1)
    big = ttk.Button(rst, text="⚡  一键恢复选中方案", style="Big.TButton",
                     command=lambda: do_restore())
    big.grid(row=0, column=0, sticky="ew", ipady=6)
    ttk.Label(rst, text="      恢复的是上方选中的方案；没选则恢复上次用的",
              foreground="#888").grid(row=1, column=0, sticky="w")

    opt = ttk.Frame(rst)
    opt.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    auto_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(opt, text="没运行的程序自动启动",
                    variable=auto_var).pack(side="left")
    ttk.Label(opt, text="    全局热键:").pack(side="left")
    hk_var = tk.StringVar(value=hotkey)
    ttk.Entry(opt, textvariable=hk_var, width=13, state="readonly").pack(side="left", padx=4)
    ttk.Button(opt, text="改", width=4, command=lambda: change_hotkey()).pack(side="left")
    ttk.Button(opt, text="保存设置", width=9,
               command=lambda: persist(True)).pack(side="right")

    logbox = ttk.LabelFrame(root, text="日志", padding=4)
    logbox.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
    ltext = tk.Text(logbox, height=5, state="disabled", font=("Consolas", 9))
    ltext.pack(fill="both", expand=True)

    # ============================================================ 逻辑
    def log(msg):
        log_lines.append(str(msg))
        del log_lines[:-200]
        ltext.config(state="normal")
        ltext.delete("1.0", "end")
        ltext.insert("1.0", "\n".join(log_lines))
        ltext.see("end")
        ltext.config(state="disabled")

    def _label(w):
        size = "最大化" if w["maximized"] else f"{w['w']}x{w['h']}"
        if w["minimized"]:
            size = "最小化"
        t = w["title"] if len(w["title"]) <= 30 else w["title"][:30] + "…"
        return f"{t}  [{size}]"

    def persist(announce=False):
        save_data(schemes, hk_var.get().strip() or "Ctrl+Alt+R", data.get("last", ""))
        refresh_scheme_list()
        if announce:
            log("💾 已保存设置")

    def refresh(keep_picks=True):
        tree.delete(*tree.get_children())
        WINDOWS.clear()
        old = set(picked) if keep_picks else set()
        if not keep_picks:
            picked.clear()
        wins = list_windows()
        for i, w in enumerate(wins):
            WINDOWS[str(i)] = w
            tree.insert("", "end", iid=str(i),
                        values=(UNCHECKED, w["proc"], _label(w)))
        # 刷新后按进程名保留勾选，并重新绑定最新窗口对象
        for iid in tree.get_children():
            proc = tree.set(iid, "proc")
            if proc in old and iid in WINDOWS:
                picked[proc] = WINDOWS[iid]
        repaint()
        log(f"窗口列表已刷新，共 {len(wins)} 个可管理窗口")

    def repaint():
        # 同一程序只保留最新一个勾选，重复的旧行取消勾
        seen = set()
        for iid in tree.get_children():
            proc = tree.set(iid, "proc")
            if proc in picked:
                if proc in seen:
                    tree.set(iid, "chk", UNCHECKED)
                    picked.pop(proc, None)
                else:
                    seen.add(proc)
                    tree.set(iid, "chk", CHECKED)
            else:
                tree.set(iid, "chk", UNCHECKED)
        sel_count.set(f"已勾选 {len(picked)} 个")

    def on_click(event):
        iid = tree.identify_row(event.y)
        if not iid:
            return
        if tree.identify_region(event.x, event.y) != "cell":
            return
        col = tree.identify_column(event.x)     # "#1" 是最左方框列
        proc = tree.set(iid, "proc")
        if col == "#1":                          # 点方框 → 勾 / 取消
            if proc in picked:
                picked.pop(proc, None)
            elif iid in WINDOWS:
                picked[proc] = WINDOWS[iid]
            repaint()
            return
        tree.selection_set(iid)
        tree.focus(iid)

    def set_all(on):
        if not on:
            picked.clear()
        else:
            for iid in tree.get_children():
                proc = tree.set(iid, "proc")
                if proc and iid in WINDOWS:
                    picked.setdefault(proc, WINDOWS[iid])
        repaint()

    def selected_scheme():
        sel = stree.selection()
        if sel:
            return schemes[int(sel[0])]
        name = data.get("last", "")
        return next((s for s in schemes if s["name"] == name), None)

    def refresh_scheme_list():
        """重建方案列表（保留当前选中项）。"""
        keep = selected_scheme()
        keep_name = keep["name"] if keep else None
        _syncing[0] = True                      # 防止 selection_set 触发递归
        stree.delete(*stree.get_children())
        for i, s in enumerate(schemes):
            stree.insert("", "end", iid=str(i), values=(f"{len(s['items'])} 个",))
        if keep_name:
            for i, s in enumerate(schemes):
                if s["name"] == keep_name:
                    stree.selection_set(str(i))
                    stree.see(str(i))
                    break
        _syncing[0] = False

    def on_scheme_pick():
        if _syncing[0]:
            return
        s = selected_scheme()
        if s:
            data["last"] = s["name"]
            log(f"选中方案「{s['name']}」，按热键或点下方按钮即可恢复")

    def save_new():
        if not picked:
            messagebox.showinfo("提示", "请先在左边勾选要记住的窗口")
            return
        cur = current_snapshots()
        if not cur:
            messagebox.showwarning("无法保存", "读不到这些窗口的程序名")
            return
        name = simpledialog.askstring("存入方案", "给这套布局起个名字：",
                                      initialvalue="工作台")
        if not name:
            return
        if any(s["name"] == name for s in schemes):
            if not messagebox.askyesno("同名", f"已有名为「{name}」的方案，要覆盖它吗？"):
                return
            schemes[schemes.index(next(s for s in schemes if s["name"] == name))] = \
                {"name": name, "items": cur}
        else:
            schemes.append({"name": name, "items": cur})
        data["last"] = name
        persist()
        log(f"💾 已存入方案「{name}」，共 {len(cur)} 个窗口")

    def current_snapshots():
        """记录当前勾选的这些窗口此刻的实际状态。

        勾选时保存的是 hwnd，这里按 hwnd 重新取一次最新窗口对象，
        这样从「勾选」到「点保存」之间如果用户又挪动了窗口，
        记下来的也是挪动后的位置。

        按进程名去重：同一个程序只保留一个窗口（勾选时后来的覆盖先前的），
        避免方案里出现两条同程序记录、恢复时都去匹配同一个窗口。
        """
        live = {w["hwnd"]: w for w in list_windows()}
        out = []
        for proc in picked:
            h = picked[proc].get("hwnd")
            win = live.get(h) if h else None
            out.append(snapshot(win if win else picked[proc]))
        return out

    def overwrite():
        s = selected_scheme()
        if not s:
            messagebox.showinfo("提示", "请先在上面选中要覆盖的方案")
            return
        if not picked:
            messagebox.showinfo("提示", "请先在左边勾选窗口")
            return
        cur = current_snapshots()
        s["items"] = cur
        persist()
        log(f"↩ 已用当前勾选的 {len(cur)} 个窗口覆盖方案「{s['name']}」")

    def rename():
        s = selected_scheme()
        if not s:
            messagebox.showinfo("提示", "请先选中方案")
            return
        nm = simpledialog.askstring("重命名", "新名字：", initialvalue=s["name"])
        if not nm or nm == s["name"]:
            return
        if any(x["name"] == nm for x in schemes):
            messagebox.showwarning("提示", "已存在同名方案")
            return
        old, s["name"] = s["name"], nm
        if data.get("last") == old:
            data["last"] = nm
        persist()
        log(f"已重命名为「{nm}」")

    def delete():
        s = selected_scheme()
        if not s:
            return
        if not messagebox.askyesno("删除方案", f"删除「{s['name']}」？"):
            return
        schemes.remove(s)
        persist()
        log(f"🗑 已删除方案「{s['name']}」")

    def change_hotkey():
        nm = simpledialog.askstring("全局热键", "输入热键（如 Ctrl+Alt+R）：",
                                    initialvalue=hk_var.get())
        if not nm:
            return
        if parse_hotkey(nm) is None:
            messagebox.showwarning("无法识别", "格式如 Ctrl+Alt+R / Win+Shift+1")
            return
        hk_var.set(nm)
        persist()
        log(f"热键已设为 {nm}，重启 WinLayout 后生效")

    def do_restore():
        s = selected_scheme()
        if not s:
            messagebox.showinfo("提示", "还没有方案。先勾选窗口，再点「存入为新方案」")
            return
        data["last"] = s["name"]
        persist()
        log(f"⚡ 恢复方案「{s['name']}」")
        root.config(cursor="watch")
        root.update_idletasks()
        try:
            ok, miss = restore(s["items"], auto_var.get(), log)
            log(f"✅ 完成，恢复 {ok} 个窗口")
            if miss:
                log(f"未运行（跳过）：{'、'.join(m['proc'] for m in miss)}")
        except Exception as e:
            log(f"❌ 出错：{e}")
        finally:
            root.config(cursor="")
        if auto_var.get():
            refresh()

    def pick_only(event=None):
        """双击某一行 → 只勾这一个（快速单选）。

        双击会先触发两次 <Button-1>，这里统一按"切成单选"处理。
        点在方框列上的双击不处理（那是想反复开关勾选）。
        """
        if event is not None:
            if tree.identify_region(event.x, event.y) != "cell":
                return
            if tree.identify_column(event.x) == "#1":
                return
        iid = tree.focus()
        if not iid:
            return
        proc = tree.set(iid, "proc")
        if not proc or iid not in WINDOWS:
            return
        picked.clear()
        picked[proc] = WINDOWS[iid]
        repaint()

    def on_hotkey():
        if selected_scheme() or schemes:
            root.after(10, do_restore)

    tree.bind("<Button-1>", lambda e: on_click(e))
    tree.bind("<Double-1>", lambda e: pick_only(e))
    stree.bind("<<TreeviewSelect>>", lambda e: on_scheme_pick())
    Hotkey(hk_var.get(), on_hotkey).start()
    root.protocol("WM_DELETE_WINDOW", lambda: (persist(), root.destroy()))

    refresh()
    refresh_scheme_list()
    if schemes:
        stree.selection_set("0")
    log(f"配置文件：{config_path()}")
    log("用法：把窗口摆好 → 勾选左边 → 点「存入为新方案」→ 以后按热键一键恢复。")
    root.mainloop()


# ================================================================ 命令行
def _out(line=""):
    if sys.stdout is None:
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(str(line) + "\n")
    sys.stdout.flush()


def main():
    args = sys.argv[1:]
    if not args:
        run_gui()
        return 0
    a0 = args[0].lower()
    data = load_data()
    schemes = data["schemes"]

    if a0 in ("-h", "--help", "/?"):
        _out("""WinLayout — 窗口位置记忆器

  WinLayout.exe                  打开界面
  WinLayout.exe --restore [名称]  静默恢复方案（省略名称则用上次的）
  WinLayout.exe --list           列出所有方案
  WinLayout.exe --path           配置文件位置
  WinLayout.exe --del 名称       删除某个方案
""")
        return 0

    if a0 == "--path":
        _out(config_path())
        return 0

    if a0 in ("--list",):
        if not schemes:
            _out("还没有任何方案")
        for i, s in enumerate(schemes):
            mark = "  ← 上次使用" if s["name"] == data.get("last") else ""
            _out(f"{i + 1}. {s['name']}（{len(s['items'])} 个窗口）{mark}")
            for it in s["items"]:
                where = "最大化" if it.get("maximized") else \
                    f"({it['x']},{it['y']}) {it['w']}x{it['h']}"
                _out(f"     · {it.get('proc',''):<20} {where}")
        return 0

    if a0 in ("--restore", "-r"):
        if not schemes:
            _out("还没有任何方案")
            return 1
        name = args[1] if len(args) > 1 else data.get("last")
        s = next((x for x in schemes if x["name"] == name), None)
        if s is None:
            _out(f"找不到方案「{name or '(空)'}」")
            return 1
        ok, _ = restore(s["items"], False, _out)
        _out(f"已恢复 {ok} 个窗口（方案「{s['name']}」）")
        return 0

    if a0 == "--del":
        if len(args) < 2:
            _out("请指定方案名")
            return 1
        left = [s for s in schemes if s["name"] != args[1]]
        if len(left) == len(schemes):
            _out(f"没有找到方案「{args[1]}」")
            return 1
        save_data(left, data["hotkey"], data.get("last", ""))
        _out(f"已删除方案「{args[1]}」")
        return 0

    _out("未知参数，用 --help 查看用法。")
    return 1


if __name__ == "__main__":
    sys.exit(main())