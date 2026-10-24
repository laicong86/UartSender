# -*- coding: utf-8 -*-
"""Windows 窗口操作封装（纯 ctypes，不依赖 pywin32）。

非 Windows 平台下所有函数都会安全返回空值，这样界面可以在 Linux/macOS 上调试。

提供的功能：
    * window_from_point(x, y)  取屏幕坐标下的顶层窗口（用于"圆圈拖到目标窗口"）
    * window_title(hwnd)       取窗口标题
    * is_window(hwnd)          窗口是否还存在
    * root_hwnd(widget_id)     Tk 窗口 id → 顶层窗口句柄
    * is_foreground(hwnd)      窗口是否在前台
    * activate_window(hwnd)    把目标窗口切到前台（带重试，尽量成功）
    * post_text(hwnd, ...)     后台消息方式逐字符投递（WM_CHAR）
    * type_text_input(...)     前台键盘模拟方式逐字符输入（SendInput）
    * list_windows()           枚举可见的顶层窗口（右键菜单选择目标用）
"""

from __future__ import annotations

import os
import sys
import time

IS_WIN = sys.platform.startswith("win")

# --------------------------------------------------------------------------- #
# 非 Windows 平台：提供空实现，保证界面层可以正常运行
# --------------------------------------------------------------------------- #
if not IS_WIN:  # pragma: no cover - 仅在非 Windows 上走这里
    def enable_dpi_awareness():
        return

    def window_from_point(x, y, exclude_pid=None):
        return None

    def window_title(hwnd):
        return ""

    def is_window(hwnd):
        return False

    def root_hwnd(widget_id):
        return None

    def is_foreground(hwnd):
        return False

    def set_topmost(hwnd, on=True):
        return False

    def set_app_user_model_id(app_id="UartSender.CommandSender"):
        return False

    def apply_exe_icon(hwnd, exe_path=None):
        return False

    def activate_window(hwnd):
        return False

    def list_windows():
        return []

    def post_text(hwnd, text, char_delay=0.0, stop=None, keydown_compat=False):
        return False

    def type_text_input(hwnd, text, char_delay=0.0, stop=None):
        return False


else:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    ULONG_PTR = ctypes.c_size_t
    WPARAM = getattr(wintypes, "WPARAM", ctypes.c_size_t)
    LPARAM = getattr(wintypes, "LPARAM", ctypes.c_ssize_t)

    GA_ROOT = 2
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_CHAR = 0x0102
    SW_RESTORE = 9
    VK_RETURN = 0x0D
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    INPUT_KEYBOARD = 1

    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1

    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010

    # ------------------------------------------------------------------ 结构体
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    # -------------------------------------------------------------- 函数签名
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.SetActiveWindow.argtypes = [wintypes.HWND]
    user32.SetActiveWindow.restype = wintypes.HWND
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                       ctypes.POINTER(wintypes.HANDLE),
                                       ctypes.POINTER(wintypes.HANDLE), wintypes.UINT]
    shell32.ExtractIconExW.restype = wintypes.UINT
    shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # ------------------------------------------------------------------ 工具
    def enable_dpi_awareness():
        """让坐标在缩放显示器上也是真实像素，否则拖拽取点会偏移。"""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass

    def window_title(hwnd):
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def is_window(hwnd):
        return bool(hwnd) and bool(user32.IsWindow(hwnd))

    def _pid_of(hwnd):
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _exe_of(pid):
        try:
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return ""
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return os.path.basename(buf.value)
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            pass
        return ""

    def window_from_point(x, y, exclude_pid=None):
        """返回屏幕坐标 (x, y) 处的顶层窗口信息 dict，失败返回 None。

        会跳过本程序自己的窗口（exclude_pid 通常是当前进程 pid）。
        """
        pt = wintypes.POINT(int(x), int(y))
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return None
        hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        pid = _pid_of(hwnd)
        if exclude_pid is not None and pid == exclude_pid:
            return None
        title = window_title(hwnd)
        if not title:
            return None
        return {
            "hwnd": hwnd,
            "title": title,
            "pid": pid,
            "exe": _exe_of(pid),
        }

    def root_hwnd(widget_id):
        """把 Tk 给的窗口 id 换成顶层窗口句柄（失败时原样返回）。"""
        try:
            hwnd = user32.GetAncestor(int(widget_id), GA_ROOT)
            return hwnd or int(widget_id)
        except Exception:
            return widget_id

    _ICON_HANDLES = []

    def set_app_user_model_id(app_id="UartSender.CommandSender"):
        """给进程一个独立 ID，任务栏才不会按 python.exe 归组、显示别人的图标。"""
        try:
            shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
            return True
        except Exception:
            return False

    def apply_exe_icon(hwnd, exe_path=None):
        """从 exe 自身的 RT_ICON 资源取图标贴到窗口上。

        onefile 打包时如果没有 --add-data 把 app.ico 带进去，Tk 就找不到图标文件，
        窗口/任务栏会退回 Python 默认图标；这里直接从 exe 资源里取，彻底不依赖外部文件。
        """
        if not hwnd:
            return False
        exe = str(exe_path or sys.executable)
        large = wintypes.HANDLE()
        small = wintypes.HANDLE()
        try:
            if shell32.ExtractIconExW(exe, 0, ctypes.byref(large),
                                      ctypes.byref(small), 1) <= 0:
                return False
            ok = False
            if large.value:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, large.value)
                ok = True
            if small.value:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small.value)
                ok = True
            _ICON_HANDLES.extend([large.value, small.value])
            return ok
        except Exception:
            return False

    def set_topmost(hwnd, on=True):
        """让窗口一直浮在最上层（Tk 的 -topmost 在某些情况下不生效时用它兜底）。"""
        if not hwnd:
            return False
        try:
            insert_after = ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST)
            return bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0,
                                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
        except Exception:
            return False

    def is_foreground(hwnd):
        """目标窗口当前是不是前台活动窗口。"""
        if not hwnd:
            return False
        try:
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def activate_window(hwnd):
        """把窗口切到前台并确保真的切过去了（带重试）。"""
        try:
            if not hwnd:
                return False
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            if user32.GetForegroundWindow() == hwnd:
                return True

            tid_me = kernel32.GetCurrentThreadId()
            tid_tgt = user32.GetWindowThreadProcessId(hwnd, None)
            fg = user32.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0

            attached = []
            for tid in (tid_tgt, tid_fg):
                if tid and tid != tid_me and tid not in attached:
                    if user32.AttachThreadInput(tid_me, tid, True):
                        attached.append(tid)
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                try:
                    user32.SetActiveWindow(hwnd)
                    user32.SetFocus(hwnd)
                except Exception:
                    pass
            finally:
                for tid in attached:
                    user32.AttachThreadInput(tid_me, tid, False)

            for _ in range(8):                       # 最多等 ~0.25s
                if user32.GetForegroundWindow() == hwnd:
                    return True
                time.sleep(0.03)
                user32.SetForegroundWindow(hwnd)
            # 最后手段：系统级切换（对某些顽固窗口有效）
            try:
                user32.SwitchToThisWindow(hwnd, True)
                time.sleep(0.05)
            except Exception:
                pass
            return bool(user32.GetForegroundWindow() == hwnd)
        except Exception:
            return False

    def list_windows():
        """枚举当前可见、带标题的顶层窗口。"""
        result = []

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                title = window_title(hwnd)
                if not title:
                    return True
                pid = _pid_of(hwnd)
                if pid == os.getpid():
                    return True
                result.append({"hwnd": hwnd, "title": title, "pid": pid, "exe": _exe_of(pid)})
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        result.sort(key=lambda w: w["title"].lower())
        return result

    # ------------------------------------------------------------ 文本投递
    def _sleep(delay, stop):
        """可被中断的 sleep，返回 False 表示收到停止请求。"""
        if delay <= 0:
            return not (stop and stop.is_set())
        deadline = time.time() + delay
        while time.time() < deadline:
            if stop and stop.is_set():
                return False
            remain = deadline - time.time()
            time.sleep(min(0.02, max(0.001, remain)))
        return not (stop and stop.is_set())

    def _post_char(hwnd, ch, keydown_compat):
        code = ord(ch)
        if keydown_compat:
            user32.PostMessageW(hwnd, WM_KEYDOWN, code, 1)
        user32.PostMessageW(hwnd, WM_CHAR, code, 1)
        if keydown_compat:
            user32.PostMessageW(hwnd, WM_KEYUP, code, 1)

    def post_text(hwnd, text, char_delay=0.0, stop=None, keydown_compat=False):
        """后台消息方式：把文本逐字符以 WM_CHAR 投递给窗口，不需要窗口在前台。

        注意：MobaXterm / Windows Terminal 等程序只在窗口处于激活状态时才处理
        WM_CHAR，纯后台投递会被丢弃——那种情况请用「临时切换」或前台键盘模拟。
        换行按配置原样投递（\\r=13、\\n=10），不再做二次转换。
        """
        if not is_window(hwnd):
            return False
        for ch in text:
            if stop is not None and stop.is_set():
                return False
            _post_char(hwnd, ch, keydown_compat)
            if not _sleep(char_delay, stop):
                return False
        return True

    # ------------------------------------------------------------ 键盘模拟
    def _send_vk(vk):
        inp = (INPUT * 2)(
            INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, vk, 0, 0, 0))),
            INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=KEYBDINPUT(0, vk, KEYEVENTF_KEYUP, 0, 0))),
        )
        user32.SendInput(2, inp, ctypes.sizeof(INPUT))

    def _send_unicode(ch):
        scan = ord(ch)
        inp = (INPUT * 2)(
            INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE, 0, 0))),
            INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))),
        )
        user32.SendInput(2, inp, ctypes.sizeof(INPUT))

    def type_text_input(hwnd, text, char_delay=0.0, stop=None):
        """前台键盘模拟方式：需要目标窗口处于激活状态（会先尝试激活）。

        \\r、\\n、\\r\\n 都只按一次回车处理，避免出现空行。
        """
        if not is_window(hwnd):
            return False
        if not activate_window(hwnd) or not is_foreground(hwnd):
            return False          # 没抢到前台就中止，绝不把命令敲到别的窗口里
        _sleep(0.12, None)
        i, n = 0, len(text)
        while i < n:
            if stop is not None and stop.is_set():
                return False
            ch = text[i]
            if ch == "\r":
                if i + 1 < n and text[i + 1] == "\n":
                    i += 1
                _send_vk(VK_RETURN)
            elif ch == "\n":
                _send_vk(VK_RETURN)
            else:
                _send_unicode(ch)
            i += 1
            if not _sleep(char_delay, stop):
                return False
        return True


if __name__ == "__main__":  # 简单自检
    enable_dpi_awareness()
    print("IS_WIN =", IS_WIN)
    print("windows at (10, 10):", window_from_point(10, 10))
    print("count:", len(list_windows()))
