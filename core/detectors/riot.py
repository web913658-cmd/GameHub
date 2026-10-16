"""Riot Client 检测（英雄联盟 / VALORANT / 云顶之弈 等）。

来源：C:\\Riot Games\\* 安装目录 + 注册表
启动：直接运行客户端 exe（Riot 没有稳定的公开启动协议）
"""

from __future__ import annotations

import os
from typing import List

from ..models import Game
from .. import winutil

# 已知产品：安装目录名 -> (显示名, 相对可执行文件)
KNOWN = {
    "League of Legends": ("League of Legends", r"LeagueClient.exe"),
    "VALORANT": ("VALORANT", r"live\VALORANT.exe"),
    "Teamfight Tactics": ("Teamfight Tactics", r"LeagueClient.exe"),
    "Legends of Runeterra": ("Legends of Runeterra", r"LoR.exe"),
    "2XKO": ("2XKO", r"2XKO.exe"),
}

RIOT_ROOTS = [r"C:\Riot Games", r"D:\Riot Games", r"E:\Riot Games"]


def _icon_exe(install: str) -> str:
    for exe in sorted(os.listdir(install)) if os.path.isdir(install) else []:
        if exe.lower().endswith(".exe"):
            return os.path.join(install, exe)
    return ""


def detect() -> List[Game]:
    games: List[Game] = []
    seen = set()
    for root in RIOT_ROOTS:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if not os.path.isdir(full):
                continue
            display, rel = KNOWN.get(entry, (entry, ""))
            exe = os.path.join(full, rel) if rel else ""
            if exe and not os.path.isfile(exe):
                exe = _icon_exe(full)
            if not exe:
                exe = _icon_exe(full)
            if not exe:
                continue
            key = full.lower()
            if key in seen:
                continue
            seen.add(key)
            games.append(Game(
                name=display,
                platform="riot",
                launch_kind="exe",
                launch_target=exe,
                install_dir=full,
                app_id=entry,
                exe=exe,
                source=f"dir @ {full}",
            ))
    return games
