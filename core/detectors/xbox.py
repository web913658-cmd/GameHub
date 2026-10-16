"""Xbox / Microsoft Store / Game Pass 检测。

方式：PowerShell Get-AppxPackage 枚举游戏类包 + 扫描 XboxGames 安装目录。
启动：explorer.exe shell:AppsFolder\\<PackageFamilyName>!<ApplicationId>
"""

from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET
from typing import List

from ..models import Game

PS_QUERY = r"""
$ErrorActionPreference='SilentlyContinue'
$pat = 'Microsoft Studios|Xbox|Bethesda|Activision|Zenimax|Mojang|343 Industries|Playground|Turn 10|The Coalition|Rare Ltd|Obsidian|inXile|Double Fine|Compulsion|Ninja Theory|Undead Labs|World''s Edge'
Get-AppxPackage | Where-Object { $_.Publisher -match $pat } | ForEach-Object {
  [PSCustomObject]@{
    Name = $_.Name
    PFN  = $_.PackageFamilyName
    Dir  = $_.InstallLocation
    Pub  = $_.Publisher
    Ver  = $_.Version
  }
} | ConvertTo-Json -Compress
"""

# 明显不是游戏的包名关键词（过滤掉运行库/组件）
SKIP_TOKENS = (
    "vclibs", "net.native", "xboxidentityprovider", "gamingapp", "xboxgameoverlay",
    "gamingoverlay", "xboxspeechtotext", "hevc", "vp9", "webmedia", "storepurchaseapp",
    "desktopappinstaller", "windowscommunicationsapps", "photos", "zune", "solitaire",
    "media", "music", "video", "camera", "maps", "people", "weather", "news",
    "officehub", "skypeapp", "getstarted", "windowsfeedbackhub",
)

XBOX_DIRS = [r"C:\XboxGames", r"D:\XboxGames", r"E:\XboxGames",
             r"C:\Program Files\WindowsApps"]


def _run_ps(script: str) -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, text=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout or ""
    except Exception:
        return ""


def _app_id_from_manifest(install_dir: str) -> str:
    manifest = os.path.join(install_dir, "AppxManifest.xml")
    if not os.path.isfile(manifest):
        return ""
    try:
        tree = ET.parse(manifest)
        root = tree.getroot()
        ns = {"a": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
        for app in root.findall(".//a:Applications/a:Application", ns):
            aid = app.get("Id")
            if aid:
                return aid
        # 无命名空间兜底
        for app in root.iter():
            if app.tag.endswith("Application") and app.get("Id"):
                return app.get("Id")
    except Exception:
        return ""
    return ""


def _from_appx() -> List[Game]:
    raw = _run_ps(PS_QUERY)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]

    games: List[Game] = []
    for item in data:
        name = (item.get("Name") or "").strip()
        pfn = (item.get("PFN") or "").strip()
        d = (item.get("Dir") or "").strip()
        if not name or not pfn:
            continue
        low = name.lower()
        if any(t in low for t in SKIP_TOKENS):
            continue
        app_id = _app_id_from_manifest(d) if d else ""
        if not app_id:
            continue
        friendly = name.split(".")[-1]
        games.append(Game(
            name=friendly,
            platform="xbox",
            launch_kind="shell",
            launch_target=f"shell:AppsFolder\\{pfn}!{app_id}",
            install_dir=d if os.path.isdir(d) else "",
            app_id=pfn,
            source="Get-AppxPackage",
        ))
    return games


def _from_xbox_dirs() -> List[Game]:
    games: List[Game] = []
    skip = {"gamesave", "gamingservices", "xbox", "temp", "cache", "downloads",
            "program files", "windowsapps", "system", "backup"}
    for root in XBOX_DIRS[:3]:
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            full = os.path.join(root, entry)
            if not os.path.isdir(full) or entry.lower() in skip:
                continue
            helper = os.path.join(full, "Content", "gamelaunchhelper.exe")
            if not os.path.isfile(helper):
                continue
            games.append(Game(
                name=entry,
                platform="xbox",
                launch_kind="exe",
                launch_target=helper,
                install_dir=full,
                app_id=entry,
                source=f"dir @ {full}",
            ))
    return games


def detect() -> List[Game]:
    merged: dict = {}
    for g in _from_appx() + _from_xbox_dirs():
        merged.setdefault((g.app_id or g.name).lower(), g)
    return list(merged.values())
