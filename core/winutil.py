"""Windows 底层工具：注册表读取、快捷方式解析、文件系统辅助。

只依赖标准库。全部为只读操作，不修改系统任何状态。
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Iterable, List, Optional

try:
    import winreg  # type: ignore
except ImportError:  # 非 Windows 平台下仅用于语法检查
    winreg = None  # type: ignore


IS_WINDOWS = sys.platform.startswith("win")


def resource_dir() -> str:
    """资源根目录（随程序分发的那份），兼容 PyInstaller 打包。

    PyInstaller 单文件模式下，data 里的文件（如 rules/*.ini、web/*）会被
    解压到临时目录 sys._MEIPASS。此时模块的 __file__ 虽然也指向该目录，
    但它对应的文件实际并不存在于磁盘上（模块来自 PYZ 压缩包），
    因此不能依赖 __file__ 拼路径去读取随包数据。

    源码运行时返回项目根目录（本文件的上一级）。
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def package_dir() -> str:
    """当前包（core/）所在目录，兼容 PyInstaller 打包。"""
    if getattr(sys, "frozen", False):
        return os.path.join(resource_dir(), "core")
    return os.path.dirname(os.path.abspath(__file__))

# 需要展开的环境变量（注册表里常见 %ProgramFiles% 之类）
_ENV_MAP = {
    "%ProgramFiles%": os.environ.get("ProgramFiles", r"C:\Program Files"),
    "%ProgramFiles(x86)%": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "%ProgramData%": os.environ.get("ProgramData", r"C:\ProgramData"),
    "%SystemDrive%": os.environ.get("SystemDrive", "C:"),
    "%LOCALAPPDATA%": os.environ.get("LOCALAPPDATA", ""),
    "%APPDATA%": os.environ.get("APPDATA", ""),
    "%USERPROFILE%": os.environ.get("USERPROFILE", ""),
}


def expand(path: str) -> str:
    if not path:
        return path
    out = os.path.expandvars(path)
    for k, v in _ENV_MAP.items():
        out = out.replace(k, v)
        out = out.replace(k.lower(), v)
    return out.replace("/", "\\")


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------

HIVES = {}
if winreg is not None:
    HIVES = {
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
    }


def reg_read(hive: str, subkey: str, value: Optional[str] = None):
    """读取注册表值。失败返回 None。"""
    if winreg is None or hive not in HIVES:
        return None
    try:
        with winreg.OpenKey(HIVES[hive], subkey, 0, winreg.KEY_READ) as k:
            if value is None:
                return winreg.QueryValueEx(k, "")[0]
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def reg_subkeys(hive: str, subkey: str) -> List[str]:
    """枚举子键名。失败返回空列表。"""
    if winreg is None or hive not in HIVES:
        return []
    out: List[str] = []
    try:
        with winreg.OpenKey(HIVES[hive], subkey, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def reg_values(hive: str, subkey: str) -> dict:
    """读取某键下所有值 -> dict。"""
    if winreg is None or hive not in HIVES:
        return {}
    out: dict = {}
    try:
        with winreg.OpenKey(HIVES[hive], subkey, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    out[name] = data
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


# 32 位程序在 64 位系统上的视图切换（很多游戏写进 WOW6432Node）
if winreg is not None:
    KEY_WOW64_32KEY = winreg.KEY_WOW64_32KEY
    KEY_WOW64_64KEY = winreg.KEY_WOW64_64KEY
else:
    KEY_WOW64_32KEY = KEY_WOW64_64KEY = 0


def reg_read_ex(hive: str, subkey: str, value: Optional[str] = None, view: int = 0):
    """带 WOW64 视图的注册表读取。"""
    if winreg is None or hive not in HIVES:
        return None
    try:
        with winreg.OpenKey(HIVES[hive], subkey, 0, winreg.KEY_READ | view) as k:
            if value is None:
                return winreg.QueryValueEx(k, "")[0]
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def reg_subkeys_ex(hive: str, subkey: str, view: int = 0) -> List[str]:
    if winreg is None or hive not in HIVES:
        return []
    out: List[str] = []
    try:
        with winreg.OpenKey(HIVES[hive], subkey, 0, winreg.KEY_READ | view) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------
# 快捷方式 (.lnk) 解析 —— 纯 stdlib，不依赖 pywin32
# --------------------------------------------------------------------------

def resolve_lnk(path: str) -> Optional[str]:
    """解析 .lnk 快捷方式指向的目标路径。

    仅实现 ShellLink 头部 + LinkInfo 的常见路径，覆盖绝大多数快捷方式。
    解析失败返回 None（调用方会降级处理）。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return None

    flags = struct.unpack_from("<I", data, 0x14)[0]
    has_link_target_idlist = bool(flags & 0x01)
    has_link_info = bool(flags & 0x02)

    pos = 0x4C
    # 跳过 LinkTargetIDList
    if has_link_target_idlist:
        if pos + 2 > len(data):
            return None
        idlist_size = struct.unpack_from("<H", data, pos)[0]
        pos += 2 + idlist_size

    if not has_link_info:
        return None

    if pos + 4 > len(data):
        return None
    link_info_size = struct.unpack_from("<I", data, pos)[0]
    link_info = data[pos : pos + link_info_size]
    if len(link_info) < 0x1C:
        return None

    li_flags = struct.unpack_from("<I", link_info, 0x08)[0]
    header_size = struct.unpack_from("<I", link_info, 0x04)[0]
    off = header_size

    local_base = ""
    if li_flags & 0x01:  # VolumeIDAndLocalBasePath
        if off + 0x10 > len(link_info):
            return None
        drive_type = struct.unpack_from("<I", link_info, off)[0]
        off += 4
        vol_label_off = struct.unpack_from("<I", link_info, off)[0]
        off += 4 + 4 + 4  # label offset + serial + label len
        # 跳过卷标字符串
        if vol_label_off:
            # 卷标从 VolumeID 结构起点算起
            base = off - 12
            end = link_info.find(b"\x00", base + vol_label_off)
            if end == -1:
                return None
            off = end + 1
        if off + 4 > len(link_info):
            return None
        local_base_off = struct.unpack_from("<I", link_info, off)[0]
        off += 4
        if local_base_off:
            start = header_size + local_base_off
            end = link_info.find(b"\x00", start)
            if end == -1:
                return None
            local_base = link_info[start:end].decode("mbcs", "ignore")

    if local_base:
        return expand(local_base)

    # 退化：从 LinkInfo 的 CommonPathSuffix 里拼
    common_suffix_off = struct.unpack_from("<I", link_info, 0x18)[0]
    if common_suffix_off:
        start = header_size + common_suffix_off
        end = link_info.find(b"\x00", start)
        if end != -1:
            suffix = link_info[start:end].decode("mbcs", "ignore")
            if suffix:
                return expand(suffix)
    return None


def lnk_arguments(path: str) -> str:
    """尝试从 .lnk 中取出命令行参数（StringData: COMMAND_LINE_ARGUMENTS）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    # 粗暴但有效：在文件尾部找可读 ASCII 参数串
    tail = data[-512:]
    text = tail.decode("mbcs", "ignore")
    return ""  # 参数提取不可靠，交给调用方按需扩展


# --------------------------------------------------------------------------
# 文件系统
# --------------------------------------------------------------------------

def start_menu_dirs() -> List[str]:
    """所有用户的开始菜单程序目录。"""
    dirs = [
        os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
    ]
    return [d for d in dirs if d and os.path.isdir(d)]


def iter_files(root: str, exts: Iterable[str], max_depth: int = 3) -> Iterable[str]:
    """在限定深度内遍历指定扩展名的文件。"""
    exts = tuple(e.lower() for e in exts)
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\").count("\\")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip("\\").count("\\") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            if fn.lower().endswith(exts):
                yield os.path.join(dirpath, fn)


def dir_size(path: str, cap: int = 0) -> int:
    """目录大小。cap>0 时超过即提前返回（避免卡住）。"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                continue
        if cap and total > cap:
            return total
    return total


def first_existing(paths: Iterable[str]) -> str:
    for p in paths:
        if p and os.path.exists(p):
            return p
    return ""
