"""Epic Games 检测。

清单：C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests\\*.item  (JSON)
启动：com.epicgames.launcher://apps/<AppName>?action=launch&silent=true
"""

from __future__ import annotations

import glob
import json
import os
from typing import List

from ..models import Game

MANIFEST_DIRS = [
    r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests",
    r"C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat",
]


def _manifests() -> List[str]:
    out: List[str] = []
    for d in MANIFEST_DIRS:
        if os.path.isdir(d):
            out.extend(glob.glob(os.path.join(d, "*.item")))
    return out


def detect() -> List[Game]:
    games: List[Game] = []
    for mf in _manifests():
        try:
            with open(mf, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            continue

        name = (data.get("DisplayName") or "").strip()
        app_name = (data.get("AppName") or "").strip()
        install = (data.get("InstallLocation") or "").strip()
        launch_exe = (data.get("LaunchExecutable") or "").strip()
        ns = (data.get("CatalogNamespace") or "").strip()
        item = (data.get("CatalogItemId") or "").strip()

        if not name or not app_name:
            continue
        # 过滤纯引擎/插件条目
        if name.lower() in ("unreal engine",):
            continue

        exe = ""
        if install and launch_exe:
            cand = os.path.join(install, launch_exe.replace("/", "\\"))
            if os.path.isfile(cand):
                exe = cand

        # Epic 协议：三种等价写法，用最通用的 AppName 形式
        if ns and item:
            uri = (f"com.epicgames.launcher://apps/{ns}%3A{item}%3A{app_name}"
                   f"?action=launch&silent=true")
        else:
            uri = f"com.epicgames.launcher://apps/{app_name}?action=launch&silent=true"

        games.append(Game(
            name=name,
            platform="epic",
            launch_kind="protocol",
            launch_target=uri,
            install_dir=install,
            app_id=app_name,
            exe=exe,
            size_bytes=int(data.get("InstallSize") or 0),
            source=f"manifest @ {mf}",
        ))
    return games
