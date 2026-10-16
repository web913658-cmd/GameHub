"""开机自启动管理。

实现方式
--------
写注册表 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`。

选注册表而不是「启动」文件夹（%APPDATA%\\...\\Startup）的理由：

* 不需要构造 .lnk 快捷方式（ShellLink 结构解析本项目已有，
  但**写入** lnk 需要 COM 接口，依赖更多）
* 注册表项直接指向 exe 路径，可带参数（本项目用 `--minimized`
  实现开机静默启动，只驻托盘不弹窗口）
* 删除时只需删值，不会残留文件

参考：Microsoft Learn 文档 `Run 键`（标准做法，非某开源项目独有）。
"""

from __future__ import annotations

import os
import sys
import winreg

APP_NAME = "GameHub"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe_command() -> str:
    """当前可执行文件的启动命令（带 --minimized 参数）。

    源码运行时用 python + 脚本路径；打包后用 exe 路径。
    """
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        return f'"{exe}" --minimized'
    script = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "GameHub.py"))
    py = sys.executable
    return f'"{py}" "{script}" --minimized'


def is_enabled() -> bool:
    """是否已设置开机自启动。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def enable() -> tuple[bool, str]:
    """开启开机自启动。返回 (成功, 消息)。"""
    cmd = _exe_command()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        return True, f"已开启开机自启动：{cmd}"
    except OSError as exc:
        return False, f"写入注册表失败：{exc}"


def disable() -> tuple[bool, str]:
    """关闭开机自启动。返回 (成功, 消息)。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        return True, "已关闭开机自启动"
    except FileNotFoundError:
        return True, "开机自启动本就未开启"
    except OSError as exc:
        return False, f"删除注册表值失败：{exc}"


def set_enabled(on: bool) -> tuple[bool, str]:
    """设置开机自启动开关。"""
    return enable() if on else disable()


def current_command() -> str:
    """当前注册表里的启动命令（未设置时返回空串）。用于排查。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(val)
    except OSError:
        return ""
