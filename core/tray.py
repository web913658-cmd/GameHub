"""系统托盘图标：让 GameHub 常驻后台。

关闭应用窗口不再退出程序，而是缩到系统托盘（任务栏右下角通知区域）。
左键点击托盘图标弹出菜单：打开 / 完全退出。

实现说明
--------
核心 Win32 调用链（Shell_NotifyIconW + 隐藏消息窗口 + TrackPopupMenuEx）
适配自 **pystray** 的 Windows 后端，原项目由 Moses Palmér 以
**LGPL-3.0** 发布：https://github.com/moses-palmer/pystray

相比 pystray 做了裁剪，以保持本项目「零第三方依赖」的约束：

1. **不需要 Pillow**。pystray 用 PIL Image 序列化成临时 ICO 再加载；
   本项目直接 `LoadImage` + `LR_LOADFROMFILE` 读 build/icon.ico，
   省掉 Pillow 与临时文件。
2. 菜单为静态两项（打开 / 完全退出），不实现 pystray 的动态菜单模型。
3. 只保留本项目用到的消息分支，不做通用事件分发。

保留了 pystray 里两个**必须**处理的细节（否则托盘行为有 bug）：

* **必须创建真正的顶层窗口**（WS_POPUP），不能用 message-only window。
  `WM_TASKBARCREATED` 是广播消息，只有顶层窗口能收到。
* **提权运行时要显式放行 `WM_TASKBARCREATED`**（ChangeWindowMessageFilterEx）。
  否则资源管理器（explorer）重启后托盘图标会凭空消失且不会恢复。
* **弹菜单前必须 SetForegroundWindow**，否则 TrackPopupMenuEx
  点击菜单外区域时不能正确关闭菜单。
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

# --------------------------------------------------------------------------
# Win32 常量
# --------------------------------------------------------------------------
WM_USER = 0x0400
WM_NOTIFY = WM_USER + 1          # 托盘回调消息（自定义，NOTIFYICONDATA.uCallbackMessage）
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_NCCREATE = 0x0081
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
# TrackPopupMenuEx 是模态的：菜单开着时会阻塞托盘线程的消息循环。
# 退出前必须先发这条消息把菜单取消掉，否则消息循环收不到 WM_QUIT。
WM_CANCELMODE = 0x001F

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIM_MODIFY = 0x00000001
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

WS_POPUP = 0x80000000

IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x00000040
LR_LOADFROMFILE = 0x00000010

TPM_RIGHTALIGN = 0x00000008
TPM_BOTTOMALIGN = 0x00000020
TPM_RETURNCMD = 0x00000100
TPM_NONOTIFY = 0x00000080         # 点击菜单外区域时不发 WM_COMMAND

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_DEFAULT = 0x00001000

MFT_SEPARATOR = 0x00000800
MFS_DEFAULT = 0x00001000
MIIM_FTYPE = 0x00000100

MSGFLT_ALLOW = 1

ID_OPEN = 1001
ID_EXIT = 1002

# TaskbarCreated 是系统运行时注册的广播消息，ID 只能运行时查询
WM_TASKBARCREATED = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")


# --------------------------------------------------------------------------
# 结构体
# --------------------------------------------------------------------------
class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 的输入结构（Wide 版本）。

    cbSize 用 sizeof 自动填充即可 —— 按 Win32 约定，API 只读取
    cbSize 所声明的有效长度，结构体比新版小也能正常工作。
    """
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# --------------------------------------------------------------------------
# 函数原型
# --------------------------------------------------------------------------
_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32

def _def(fn, argtypes, restype):
    fn.argtypes = argtypes
    fn.restype = restype
    return fn


_def(_shell32.Shell_NotifyIconW,
     [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)], wintypes.BOOL)
_def(_user32.RegisterClassExW, [ctypes.POINTER(WNDCLASSEXW)], wintypes.ATOM)
_def(_user32.UnregisterClassW,
     [wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL)
_def(_user32.CreateWindowExW,
     [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
      wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID],
     wintypes.HWND)
_def(_user32.DestroyWindow, [wintypes.HWND], wintypes.BOOL)
_def(_user32.DefWindowProcW,
     [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
     ctypes.c_ssize_t)
_def(_user32.GetMessageW,
     [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
      wintypes.UINT, wintypes.UINT], ctypes.c_ssize_t)
_def(_user32.PeekMessageW,
     [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
      wintypes.UINT, wintypes.UINT, wintypes.UINT], wintypes.BOOL)
_def(_user32.TranslateMessage,
     [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL)
_def(_user32.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)],
     ctypes.c_ssize_t)
_def(_user32.PostQuitMessage, [ctypes.c_int], None)
_def(_user32.SendMessageW,
     [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
     ctypes.c_ssize_t)
_def(_user32.LoadImageW,
     [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
      ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.HANDLE)
_def(_user32.DestroyIcon, [wintypes.HICON], wintypes.BOOL)
_def(_user32.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL)
_def(_user32.GetCursorPos,
     [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL)
# GetModuleHandleW 在 kernel32，不在 user32
_def(_kernel32.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HINSTANCE)
_def(_user32.ChangeWindowMessageFilterEx,
     [wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.LPVOID],
     wintypes.BOOL)
_def(_user32.CreatePopupMenu, [], wintypes.HMENU)
_def(_user32.AppendMenuW,
     [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR],
     wintypes.BOOL)
_def(_user32.TrackPopupMenuEx,
     [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
      wintypes.HWND, wintypes.LPVOID], wintypes.UINT)
_def(_user32.DestroyMenu, [wintypes.HMENU], wintypes.BOOL)

PM_NOREMOVE = 0x0000


# --------------------------------------------------------------------------
# 托盘图标
# --------------------------------------------------------------------------
class TrayIcon:
    """常驻系统托盘的图标。

    用法::

        tray = TrayIcon("GameHub", icon_path, on_open=..., on_exit=...)
        tray.start()          # 后台线程跑消息循环
        ...
        tray.stop()           # 移除图标、结束消息循环

    回调在托盘线程被调用，实现方需自行保证线程安全。
    """

    def __init__(self, tooltip: str, icon_path: str,
                 on_open=None, on_exit=None):
        self.tooltip = tooltip[:127]
        self.icon_path = icon_path
        self.on_open = on_open
        self.on_exit = on_exit

        self._atom = 0
        self._hwnd = None
        self._hicon = None
        self._running = False
        self._thread = None
        self._visible = False
        self._wndproc = None          # 必须持有引用，否则被 GC 后崩溃

    # -- 公开接口 --------------------------------------------------------
    def start(self) -> bool:
        """启动托盘线程。返回是否成功。"""
        if self._running:
            return True
        if not os.path.isfile(self.icon_path):
            return False
        try:
            self._hicon = _user32.LoadImageW(
                None, self.icon_path, IMAGE_ICON,
                0, 0, LR_DEFAULTSIZE | LR_LOADFROMFILE)
            if not self._hicon:
                return False
        except OSError:
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="GameHub-tray", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """移除托盘图标并结束消息循环。可重复调用。"""
        if not self._running:
            return
        self._running = False
        if self._hwnd:
            # 若菜单正开着（TrackPopupMenuEx 阻塞中），先取消它，
            # 否则消息循环收不到 WM_QUIT、线程无法退出
            _user32.SendMessageW(self._hwnd, WM_CANCELMODE, 0, 0)
            _user32.PostQuitMessage(0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._atom:
            _user32.UnregisterClassW(
                ctypes.cast(self._atom, wintypes.LPCWSTR),
                _kernel32.GetModuleHandleW(None))
            self._atom = 0
        if self._hicon:
            _user32.DestroyIcon(self._hicon)
            self._hicon = None

    @property
    def visible(self) -> bool:
        return self._visible

    # -- 内部实现 --------------------------------------------------------
    def _run(self) -> None:
        """托盘线程主体：建窗口 -> 注册图标 -> 跑消息循环。"""
        try:
            self._atom = self._register_class()
            if not self._atom:
                self._running = False
                return

            self._hwnd = self._create_window()
            if not self._hwnd:
                self._running = False
                return

            self._show()

            msg = wintypes.MSG()
            lpmsg = ctypes.byref(msg)
            while True:
                r = _user32.GetMessageW(lpmsg, None, 0, 0)
                if r == 0 or r == -1:
                    break
                _user32.TranslateMessage(lpmsg)
                _user32.DispatchMessageW(lpmsg)

        finally:
            # 收尾：删图标、销毁窗口、注销类
            if self._hwnd:
                self._hide()
                _user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if self._atom:
                _user32.UnregisterClassW(
                    ctypes.cast(self._atom, wintypes.LPCWSTR),
                    _kernel32.GetModuleHandleW(None))
                self._atom = 0
            if self._hicon:
                _user32.DestroyIcon(self._hicon)
                self._hicon = None
            self._running = False
            self._visible = False

    def _register_class(self) -> int:
        """注册窗口类。类名带 id 保证多实例不冲突。"""
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        # WNDPROC 必须存活到窗口销毁，故存到实例上防 GC
        self._wndproc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
            wintypes.WPARAM, wintypes.LPARAM)(self._wnd_proc)
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = 1          # COLOR_WINDOW + 1
        wc.lpszMenuName = None
        wc.lpszClassName = f"GameHubTray{id(self)}"
        wc.hIconSm = None
        return _user32.RegisterClassExW(ctypes.byref(wc))

    def _create_window(self):
        """创建隐藏的顶层窗口。

        必须是顶层窗口：WM_TASKBARCREATED 是广播消息，
        message-only window 收不到。
        """
        hwnd = _user32.CreateWindowExW(
            0,
            ctypes.cast(self._atom, wintypes.LPCWSTR),
            None,
            WS_POPUP,
            0, 0, 0, 0,
            None, None,
            _kernel32.GetModuleHandleW(None),
            None)
        if not hwnd:
            return None

        # 提权运行时必须显式放行 WM_TASKBARCREATED，
        # 否则 explorer 重启后托盘图标消失且不恢复
        _user32.ChangeWindowMessageFilterEx(
            hwnd, WM_TASKBARCREATED, MSGFLT_ALLOW, None)
        return hwnd

    def _message(self, code: int, flags: int, **kw) -> None:
        """调用 Shell_NotifyIconW。"""
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = id(self) & 0xFFFFFFFF
        data.uFlags = flags
        for k, v in kw.items():
            setattr(data, k, v)
        _shell32.Shell_NotifyIconW(code, ctypes.byref(data))

    def _show(self) -> None:
        self._message(
            NIM_ADD,
            NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage=WM_NOTIFY,
            hIcon=self._hicon,
            szTip=self.tooltip)
        self._visible = True

    def _hide(self) -> None:
        self._message(NIM_DELETE, 0)
        self._visible = False

    def _popup_menu(self) -> None:
        """弹出菜单。左键和右键都触发，符合用户要求的交互。"""
        # TrackPopupMenuEx 只有在本窗口是前台窗口时才正常工作
        # （否则点菜单外区域不会关闭菜单）
        _user32.SetForegroundWindow(self._hwnd)

        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))

        hmenu = _user32.CreatePopupMenu()
        try:
            _user32.AppendMenuW(
                hmenu, MF_STRING | MF_DEFAULT, ID_OPEN,
                "打开 GameHub")
            _user32.AppendMenuW(
                hmenu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(
                hmenu, MF_STRING, ID_EXIT, "完全退出")

            cmd = _user32.TrackPopupMenuEx(
                hmenu,
                TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD
                | TPM_NONOTIFY,
                point.x, point.y,
                self._hwnd, None)
        finally:
            _user32.DestroyMenu(hmenu)

        if cmd == ID_OPEN:
            if self.on_open:
                self.on_open()
        elif cmd == ID_EXIT:
            if self.on_exit:
                self.on_exit()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """窗口过程。注意：在托盘线程被调用。"""
        if msg == WM_NCCREATE:
            return True
        if msg == WM_CREATE:
            return 0
        if msg == WM_DESTROY:
            return 0

        if msg == WM_NOTIFY:
            # lparam 是产生通知的鼠标/键盘消息
            if lparam in (WM_LBUTTONUP, WM_RBUTTONUP):
                try:
                    self._popup_menu()
                except Exception:
                    pass
            return 0

        if msg == WM_TASKBARCREATED:
            # 资源管理器重启后重新注册，托盘图标才能回来
            if self._visible and self._hicon:
                try:
                    self._show()
                except Exception:
                    pass
            return 0

        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def icon_path() -> str:
    """托盘图标文件路径（兼容 PyInstaller 打包）。"""
    if getattr(__import__("sys"), "frozen", False):
        root = getattr(__import__("sys"), "_MEIPASS", "")
        return os.path.join(root, "build", "icon.ico")
    # 源码模式：本文件位于 core/，项目根目录是上一级
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "build", "icon.ico")
