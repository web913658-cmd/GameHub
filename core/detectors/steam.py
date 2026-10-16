"""Steam 游戏检测。

来源：
  HKCU\\Software\\Valve\\Steam            -> SteamPath
  HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam -> InstallPath
  <steam>\\steamapps\\libraryfolders.vdf  -> 所有库文件夹
  <lib>\\steamapps\\appmanifest_*.acf     -> 每个游戏清单
启动：steam://rungameid/<appid>
"""

from __future__ import annotations

import os
import re
import glob
from typing import List

from ..models import Game
from .. import vdf, winutil

# 这些不是游戏，是运行库 / 工具，扫描时跳过
SKIP_APPIDS = {
    "228980",    # Steamworks Common Redistributables
    "1070560",   # Steam Linux Runtime
    "1391110",   # Steam Linux Runtime - Soldier
    "1628350",   # Steam Linux Runtime - Sniper
    "1070562",   # Steam Linux Runtime - Container
}
SKIP_NAME_TOKENS = (
    "redistributable", "steam linux runtime", "steamworks sdk",
    "proton", "steamvr", "steam audio", "dedicated server",
)


def _steam_root() -> str:
    for hive, key, val in (
        ("HKCU", r"Software\Valve\Steam", "SteamPath"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        p = winutil.reg_read(hive, key, val)
        if p and os.path.isdir(p):
            return os.path.normpath(p)
    # 常见默认路径兜底
    for p in (
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"E:\Steam",
    ):
        if os.path.isdir(p):
            return p
    return ""


def _library_roots(root: str) -> List[str]:
    """返回所有 Steam 库根目录（含 root 自身）。"""
    roots = [root]
    lf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.isfile(lf):
        try:
            data = vdf.parse_file(lf)
            folders = data.get("libraryfolders", data.get("LibraryFolders", {}))
            for _k, v in folders.items():
                if isinstance(v, dict):
                    path = v.get("path") or v.get("Path")
                    if path:
                        path = os.path.normpath(winutil.expand(path))
                        if os.path.isdir(path) and path not in roots:
                            roots.append(path)
                elif isinstance(v, str) and os.path.isdir(v):
                    # 旧版格式："1" "D:\\SteamLibrary"
                    p = os.path.normpath(winutil.expand(v))
                    if p not in roots and os.path.isdir(p):
                        roots.append(p)
        except Exception:
            pass
    return roots


def _find_main_exe(common_dir: str, installdir: str) -> str:
    """在游戏目录里找一个最像主程序的可执行文件。

    策略：广度优先扫到两层深，排除工具类程序，优先「名字与安装目录匹配」，
    否则取体积最大的——游戏主程序一定比附带的工具大。
    """
    game_dir = os.path.join(common_dir, installdir)
    if not os.path.isdir(game_dir):
        return ""

    bad = ("unins", "setup", "vcredist", "dxsetup", "dxwebsetup", "dotnetfx",
           "oalinst", "crash", "handler", "redist", "installer", "touchup",
           "7z", "zip", "webengine", "sendrpt", "report", "helper", "service",
           "update", "config", "unrealcefsubprocess", "cefsharp", "notification",
           "benchmark", "dedicated", "server", "vconsole", "console")

    cands: list = []
    queue = [(game_dir, 0)]
    while queue:
        d, depth = queue.pop(0)
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < 3:      # 很多游戏主程序在 Binaries/Win64/ 这类第三层
                                queue.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".exe"):
                            n = e.name.lower()
                            if any(t in n for t in bad):
                                continue
                            cands.append((e.path, e.stat().st_size))
                    except OSError:
                        continue
        except OSError:
            continue

    if not cands:
        return ""

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    key = norm(installdir)
    # 体积大的优先
    for path, _size in sorted(cands, key=lambda x: -x[1]):
        stem = norm(os.path.splitext(os.path.basename(path))[0])
        if stem and key and (stem in key or key in stem):
            return path
    return sorted(cands, key=lambda x: -x[1])[0][0]


def detect() -> List[Game]:
    root = _steam_root()
    if not root:
        return []

    games: List[Game] = []
    for lib in _library_roots(root):
        apps = os.path.join(lib, "steamapps")
        if not os.path.isdir(apps):
            continue
        common = os.path.join(apps, "common")
        for acf in glob.glob(os.path.join(apps, "appmanifest_*.acf")):
            try:
                data = vdf.parse_file(acf)
            except Exception:
                continue
            state = data.get("AppState", data)
            appid = str(state.get("appid", "")).strip()
            name = str(state.get("name", "")).strip()
            installdir = str(state.get("installdir", "")).strip()
            if not appid or not name:
                continue
            if appid in SKIP_APPIDS:
                continue
            if any(t in name.lower() for t in SKIP_NAME_TOKENS):
                continue
            size = 0
            try:
                size = int(state.get("SizeOnDisk", 0))
            except (TypeError, ValueError):
                size = 0
            exe = _find_main_exe(common, installdir) if installdir else ""
            games.append(Game(
                name=name,
                platform="steam",
                launch_kind="protocol",
                launch_target=f"steam://rungameid/{appid}",
                install_dir=os.path.join(common, installdir) if installdir else "",
                app_id=appid,
                exe=exe,
                size_bytes=size,
                source=f"appmanifest @ {acf}",
            ))
    return games
