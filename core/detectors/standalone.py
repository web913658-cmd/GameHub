"""独立游戏（非平台安装）检测。

来源一：开始菜单快捷方式，解析 .lnk 指向的 exe。
来源二：常见游戏根目录，按引擎特征文件识别。
来源三：卸载注册表项中含游戏特征的条目。

引擎特征（参考 WindowsGameExport）：
  Unity       -> UnityPlayer.dll / *_Data 目录
  Unreal      -> Engine 目录 / *-Win64-Shipping.exe
  Godot       -> *.pck
  RPG Maker   -> Game.exe + RGSS*.dll
  Ren'Py      -> renpy 目录
"""

from __future__ import annotations

import glob
import os
from typing import List, Set

from ..models import Game
from .. import winutil

# 这些目录属于已知平台，交给对应检测器，避免重复
PLATFORM_DIR_TOKENS = (
    "steamapps", "steam\\steamapps", "epic games", "riot games", "xboxgames",
    "gog games", "gog galaxy", "battle.net", "blizzard", "ubisoft",
    "ea games", "electronic arts", "origin games", "amazon games", "windowsapps",
    "\\windows\\", "\\program files\\windows", "\\appdata\\local\\temp",
)

# 明显不是游戏的可执行文件名
BAD_EXE_TOKENS = (
    "unins", "setup", "installer", "update", "updater", "launcher_installer",
    "vcredist", "dxsetup", "dxwebsetup", "dotnet", "report", "crashreport",
    "crashhandler", "eacore", "eabackground", "helper", "service", "config",
    "readme", "editor", "server", "dedicated", "tool", "benchmark", "unrealcefsubprocess",
    "notification_helper", "python", "node", "java", "ffmpeg",
)

SCAN_ROOTS = [
    r"C:\Games", r"D:\Games", r"E:\Games", r"F:\Games",
    r"C:\Game", r"D:\Game",
    r"D:\Program Files", r"D:\Program Files (x86)",
    r"E:\Program Files", r"E:\Program Files (x86)",
    r"D:\SteamLibrary\steamapps\common", r"E:\SteamLibrary\steamapps\common",
]

ENGINE_MARKERS = {
    "Unity": ("UnityPlayer.dll", "*_Data"),
    "Unreal": ("Engine", "*-Win64-Shipping.exe"),
    "Godot": ("*.pck",),
    "RPGMaker": ("Game.exe", "RGSS*.dll"),
    "RenPy": ("renpy",),
}


def _looks_like_game_dir(path: str) -> tuple:
    """返回 (是否游戏目录, 引擎名)。"""
    try:
        entries = os.listdir(path)
    except OSError:
        return False, ""
    low = {e.lower() for e in entries}
    for engine, markers in ENGINE_MARKERS.items():
        for m in markers:
            if "*" in m:
                if glob.glob(os.path.join(path, m)):
                    return True, engine
            else:
                if m.lower() in low:
                    return True, engine
    return False, ""


def _pick_exe(path: str) -> str:
    best = ""
    try:
        for exe in sorted(glob.glob(os.path.join(path, "*.exe"))):
            n = os.path.basename(exe).lower()
            if any(t in n for t in BAD_EXE_TOKENS):
                continue
            if "shipping" in n:      # Unreal 主程序优先
                return exe
            if not best:
                best = exe
    except OSError:
        pass
    return best


def _is_platform_path(p: str) -> bool:
    lp = p.lower()
    return any(t in lp for t in PLATFORM_DIR_TOKENS)


def _from_start_menu() -> List[Game]:
    games: List[Game] = []
    for base in winutil.start_menu_dirs():
        for lnk in winutil.iter_files(base, (".lnk",), max_depth=4):
            target = winutil.resolve_lnk(lnk)
            if not target or not target.lower().endswith(".exe"):
                continue
            if not os.path.isfile(target):
                continue
            if _is_platform_path(target):
                continue
            n = os.path.basename(target).lower()
            if any(t in n for t in BAD_EXE_TOKENS):
                continue
            name = os.path.splitext(os.path.basename(lnk))[0]
            install = os.path.dirname(target)
            is_game, engine = _looks_like_game_dir(install)
            # 开始菜单里的短名字 + 独立目录，默认认为是游戏/应用
            games.append(Game(
                name=name,
                platform="standalone",
                launch_kind="exe",
                launch_target=target,
                install_dir=install,
                exe=target,
                source=f"开始菜单 ({engine or 'shortcut'})",
            ))
    return games


def _from_roots() -> List[Game]:
    games: List[Game] = []
    for root in SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(root, entry)
            if not os.path.isdir(full):
                continue
            if _is_platform_path(full):
                continue
            is_game, engine = _looks_like_game_dir(full)
            if not is_game:
                # 再看一层子目录
                found = False
                for sub in list(entries)[:0]:
                    pass
                for sub in (os.listdir(full) if os.path.isdir(full) else [])[:40]:
                    subdir = os.path.join(full, sub)
                    if os.path.isdir(subdir):
                        ok, eng = _looks_like_game_dir(subdir)
                        if ok:
                            is_game, engine, full = True, eng, subdir
                            found = True
                            break
                if not found:
                    continue
            exe = _pick_exe(full)
            if not exe:
                continue
            games.append(Game(
                name=os.path.basename(full.rstrip("\\")),
                platform="standalone",
                launch_kind="exe",
                launch_target=exe,
                install_dir=full,
                exe=exe,
                source=f"目录扫描 ({engine})",
            ))
    return games


def detect() -> List[Game]:
    merged: dict = {}
    for g in _from_start_menu() + _from_roots():
        key = (g.install_dir or g.exe or g.name).lower()
        if key in merged:
            continue
        merged[key] = g
    return list(merged.values())
