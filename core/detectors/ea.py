"""EA App / Origin 检测。

注册表：HKLM\\SOFTWARE\\WOW6432Node\\EA Games\\<Game>  -> Install Dir
        HKLM\\SOFTWARE\\WOW6432Node\\Electronic Arts\\<Game>
清单：C:\\ProgramData\\EA Desktop\\InstalledContent.csv
      C:\\ProgramData\\Origin\\LocalContent\\*.mfst
启动：直接运行 exe，或 origin2://game/launch?offerIds=<id>
"""

from __future__ import annotations

import csv
import glob
import os
from typing import List

from ..models import Game
from .. import winutil

SKIP = ("EA Core", "EA Desktop", "Origin", "EA app", "EABackgroundService")


def _find_exe(install: str) -> str:
    if not install or not os.path.isdir(install):
        return ""
    for exe in sorted(glob.glob(os.path.join(install, "*.exe"))):
        n = os.path.basename(exe).lower()
        if any(w in n for w in ("unins", "touchup", "setup", "eacore", "eabackground")):
            continue
        return exe
    # 下一层
    for sub in sorted(os.listdir(install)):
        subdir = os.path.join(install, sub)
        if os.path.isdir(subdir):
            for exe in sorted(glob.glob(os.path.join(subdir, "*.exe"))):
                n = os.path.basename(exe).lower()
                if not any(w in n for w in ("unins", "touchup", "setup")):
                    return exe
    return ""


def _from_registry() -> List[Game]:
    games: List[Game] = []
    for base in (r"SOFTWARE\WOW6432Node\EA Games",
                 r"SOFTWARE\WOW6432Node\Electronic Arts",
                 r"SOFTWARE\Electronic Arts"):
        for sub in winutil.reg_subkeys_ex("HKLM", base, winutil.KEY_WOW64_32KEY):
            if sub in SKIP:
                continue
            full = f"{base}\\{sub}"
            install = winutil.reg_read_ex("HKLM", full, "Install Dir", winutil.KEY_WOW64_32KEY)
            if not install:
                install = winutil.reg_read_ex("HKLM", full, "InstallLocation",
                                              winutil.KEY_WOW64_32KEY)
            install = winutil.expand(str(install or ""))
            if not install or not os.path.isdir(install):
                continue
            exe = _find_exe(install)
            games.append(Game(
                name=sub,
                platform="ea",
                launch_kind="exe" if exe else "protocol",
                launch_target=exe or f"origin2://game/launch?offerIds={sub}",
                install_dir=install,
                app_id=sub,
                exe=exe,
                source=f"registry @ {full}",
            ))
    return games


def _from_csv() -> List[Game]:
    path = r"C:\ProgramData\EA Desktop\InstalledContent.csv"
    if not os.path.isfile(path):
        return []
    games: List[Game] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("DisplayName") or row.get("Name") or "").strip()
                install = winutil.expand((row.get("InstallLocation") or "").strip())
                if not name or not os.path.isdir(install):
                    continue
                exe = _find_exe(install)
                games.append(Game(
                    name=name,
                    platform="ea",
                    launch_kind="exe" if exe else "protocol",
                    launch_target=exe or f"origin2://game/launch?offerIds={row.get('OfferId','')}",
                    install_dir=install,
                    app_id=str(row.get("ContentId") or row.get("OfferId") or name),
                    exe=exe,
                    source="InstalledContent.csv",
                ))
    except Exception:
        return []
    return games


def detect() -> List[Game]:
    merged: dict = {}
    for g in _from_registry() + _from_csv():
        key = (g.install_dir or g.name).lower()
        merged.setdefault(key, g)
    return list(merged.values())
