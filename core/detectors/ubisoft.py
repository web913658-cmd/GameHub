"""Ubisoft Connect 检测。

注册表：HKLM\\SOFTWARE\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\<id>
  值：InstallDir / InstallId
启动：uplay://launch/<id>/0  或直接运行 exe
"""

from __future__ import annotations

import glob
import os
from typing import List

from ..models import Game
from .. import winutil


def _find_exe(install: str) -> str:
    if not install or not os.path.isdir(install):
        return ""
    for exe in sorted(glob.glob(os.path.join(install, "*.exe"))):
        n = os.path.basename(exe).lower()
        if any(w in n for w in ("unins", "setup", "upc", "ubisoftconnect", "uplay")):
            continue
        return exe
    for sub in sorted(os.listdir(install)):
        subdir = os.path.join(install, sub)
        if os.path.isdir(subdir):
            for exe in sorted(glob.glob(os.path.join(subdir, "*.exe"))):
                n = os.path.basename(exe).lower()
                if not any(w in n for w in ("unins", "setup", "upc", "uplay")):
                    return exe
    return ""


def detect() -> List[Game]:
    games: List[Game] = []
    for base in (r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs",
                 r"SOFTWARE\Ubisoft\Launcher\Installs"):
        for sub in winutil.reg_subkeys_ex("HKLM", base, winutil.KEY_WOW64_32KEY):
            full = f"{base}\\{sub}"
            install = winutil.reg_read_ex("HKLM", full, "InstallDir", winutil.KEY_WOW64_32KEY)
            install = winutil.expand(str(install or ""))
            if not install or not os.path.isdir(install):
                continue
            # 名称取自安装目录最后一段
            name = os.path.basename(install.rstrip("\\")) or sub
            exe = _find_exe(install)
            games.append(Game(
                name=name,
                platform="ubisoft",
                launch_kind="protocol",
                launch_target=f"uplay://launch/{sub}/0",
                install_dir=install,
                app_id=sub,
                exe=exe,
                source=f"registry @ {full}",
            ))
    return games
