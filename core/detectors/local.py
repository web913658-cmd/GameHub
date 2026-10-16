"""本地安装游戏检测（非商店渠道）。

覆盖范围：
  · 免安装 / 绿色版（解压即玩）
  · 离线安装包安装的游戏（GOG 离线版、场景发布、各种打包版）
  · 老游戏、光盘版、DRM-free 游戏
  · 自己编译或从 itch.io 等下载的独立游戏

判定思路（借鉴 SteamDB FileDetectionRuleSets 的两遍匹配）：
  1. 引擎特征：命中 Unity / Unreal / Godot / GameMaker / RPG Maker / Ren'Py 等
     强特征文件，基本可以确定这是游戏。
  2. 本地安装标记：steam_api.dll / steam_emu.ini / goggame-*.info / *.nfo /
     打包组残留目录 —— 这些是"离线安装的游戏"最典型的特征。
  3. 结构特征：存在大体积主程序 + 运行库目录（_CommonRedist / DirectX / vcredist），
     普通应用不会同时满足。

本模块只做**只读的文件名识别**，不下载、不修改、不涉及任何破解行为。
"""

from __future__ import annotations

import os
import re
import time
from typing import Dict, List, Optional, Set, Tuple

from ..models import Game
from .. import winutil
from ..engines import detect_engine

# --------------------------------------------------------------------------
# 扫描范围
# --------------------------------------------------------------------------

# 这些目录名是"容器"，游戏在它们的下一层
CONTAINER_NAMES = {
    "games", "game", "游戏", "pc games", "my games", "gamers",
    "program files", "program files (x86)", "steamlibrary", "steam library",
    "epic games", "gog games", "riot games", "xboxgames", "origin games",
    "ea games", "ubisoft", "battle.net", "amazon games", "programs",
    "portable", "portables", "green", "免安装", "单机游戏", "游戏库",
}

# 直接跳过，避免浪费时间与误报
SKIP_DIR_NAMES = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "appdata", "perflogs", "recovery", "msocache", "temp", "tmp", "cache",
    "node_modules", ".git", "__pycache__", ".cache", ".vscode", ".idea",
    "common files", "windowsapps", "windows defender", "internet explorer",
    "microsoft", "microsoft office", "microsoft.net", "dotnet", "python",
    "java", "git", "7-zip", "winrar", "nvidia", "amd", "intel", "drivers",
    "realtek", "conexant", "installshield installation information",
    "uninstall information", "winsxs", "assembly", "reference assemblies",
    "windows kits", "windows media player", "windows nt", "windows photo viewer",
    "msbuild", "windows powershell", "windowsapps", "onedrive", "dropbox",
    "google", "mozilla", "tencent", "wechat", "qq", "wps", "360", "sogou",
    "visual studio", "vs", "code", "docker", "vmware", "virtualbox",
}

# 这些目录属于已知游戏平台，由各自的检测器负责，本地扫描直接跳过
PLATFORM_DIR_NAMES = {
    "steam", "steam library", "steamlibrary", "epic games", "gog games",
    "gog galaxy", "riot games", "xboxgames", "origin games", "ea games",
    "electronic arts", "ubisoft", "battle.net", "blizzard", "amazon games",
    "windowsapps", "steamapps",
}

# 明确不是游戏的可执行文件
BAD_EXE_TOKENS = (
    "unins", "setup", "install", "update", "updater", "launcher_installer",
    "vcredist", "dxsetup", "dxwebsetup", "dotnetfx", "oalinst", "report",
    "crashreport", "crashhandler", "crashpad", "werfault", "eacore",
    "eabackground", "helper", "service", "config", "readme", "editor",
    "dedicated", "benchmark", "unrealcefsubprocess", "cefsharp", "notification",
    "python", "node", "java", "ffmpeg", "7z", "winrar", "uninstall",
)

# 判定为"游戏"的引擎强信号
# 注意：JVM / PyGame 这类引擎同时被大量非游戏软件使用，故意不列入
STRONG_ENGINES = {
    "Unity", "Unreal", "Godot", "GameMaker", "RPGMaker", "RenPy", "CryEngine",
    "Source", "Source2", "RE_Engine", "REDengine", "Frostbite", "Creation_Engine",
    "idTech", "IW_Engine", "GoldSource", "Anvil", "Snowdrop", "Dunia",
    "Essence_Engine", "Glacier", "Unigine", "X-Ray", "Prism3D", "Love2D",
    "ClickTeamFusion", "Construct", "GDevelop", "AdventureGameStudio",
    "KiriKiri", "NScripter", "Wintermute", "VisionaireStudio", "Defold",
    "Solar2D", "MonoGame", "FNA", "XNA", "Stride", "FlatRedBall", "Blitz3D",
    "RealVirtuality", "RAGE", "Apex_Engine", "Pico8", "WolfRPGEditor",
    "SRPG_Studio", "Bakin", "SmileGameBuilder", "TyranoBuilder", "Phyre",
    "Lime_OR_OpenFL", "HashLink", "Heaps", "NovaLogic_Engine", "TelltaleTool",
    "Serious_Engine", "GIANTS_Engine", "Zouna", "Silk", "Marmalade",
    "Divinity_Engine", "HaemimontSol", "Bitsquid", "3D_GameStudio",
    "GameGuru", "AppGameKit", "Xors3D", "Fox_Engine", "GameBox", "Katana",
    "Nitrous", "IW_Engine", "Kex", "Vision", "BlenderGameEngine",
    "FalcoEngine", "FPS_Creator", "PopcapGamesFramework", "PlayFirstPlayground",
    "ChoiceScript", "Danmakufu", "Fase", "FBN", "Flexi", "KMY", "Liquid",
    "Rayne", "SakanaGL", "SmileGameBuilder", "S2_Engine_HD", "EngineX",
    "CTG_OR_Geo-Mod", "C4_Engine", "Alpha_Engine", "001GameCreator",
}

# 本地离线安装标记（说明这不是从官方商店直接启动的游戏）
LOCAL_MARKERS = {
    "Steam_API", "Steam_Emu", "Steam_Emu_Settings", "GOG_Offline",
    "Scene_NFO", "Scene_SFV",
    "Repack_FitGirl", "Repack_DODI", "Repack_Xatab", "Repack_RGMechanics",
    "Repack_RGMechanics2", "Repack_ElAmigos", "Repack_Corepack", "Repack_MR DJ",
    "Scene_Group_CODEX", "Scene_Group_PLAZA", "Scene_Group_RUNE",
    "Scene_Group_EMPRESS", "Scene_Group_FLT", "Scene_Group_TiNYiSO",
    "Scene_Group_SKIDROW", "Scene_Group_RELOADED", "Scene_Group_PROPHET",
    "Scene_Group_CPY",
}

# 单独出现不足以判定，需要与其它证据配合
WEAK_MARKERS = {"Steam_API"}

REDIST_MARKERS = {"Redist", "Redist2", "Redist3"}
DATA_MARKERS = {"Data_Folder", "Content_Folder", "Assets_Folder"}

# 目录名里的发布组 / 打包标记（高精度信号，直接看文件夹名字）
RELEASE_TAGS = (
    "fitgirl", "dodi", "xatab", "r.g. mechanics", "rg mechanics", "elamigos",
    "corepack", "repack", "codex", "plaza", "rune", "empress", "skidrow",
    "reloaded", "prophet", "tinyiso", "cpy", "goldberg", "gog", "portable",
    "免安装", "绿色版", "中文版", "整合版", "豪华版", "典藏版",
    "[reload", "-repack", "proper", "multi", "iso",
)

# 大体积主程序阈值
BIG_EXE = 20 * 1024 * 1024     # 20 MB
HUGE_EXE = 60 * 1024 * 1024    # 60 MB
BIG_DIR = 1_200 * 1024 * 1024  # 1.2 GB，游戏目录的典型下限

# 展示用的大小上限。判定时用 BIG_DIR 提前收手，但展示给用户的必须是
# 整个目录的真实体积，所以这里给一个足够大的上限（4 TB），
# 等效于「不设上限」，只在极端情况下兜底。
SIZE_CAP = 4 * 1024 ** 4

MAX_CANDIDATES = 600           # 最多检查多少个候选目录
TIME_BUDGET = 30.0             # 秒，超时即返回已找到的结果


# --------------------------------------------------------------------------
# 盘符
# --------------------------------------------------------------------------

def fixed_drives() -> List[str]:
    """枚举本机固定磁盘盘符。"""
    drives: List[str] = []
    # Python 3.12+ 提供 os.listdrives()
    lister = getattr(os, "listdrives", None)
    if lister is not None:
        try:
            for d in lister():
                if os.path.isdir(d):
                    drives.append(d.rstrip("\\/"))
            if drives:
                return drives
        except OSError:
            pass
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                letter = chr(ord("A") + i) + ":"
                # DRIVE_FIXED = 3
                dtype = ctypes.windll.kernel32.GetDriveTypeW(letter + "\\")
                if dtype == 3:
                    drives.append(letter)
    except Exception:
        pass
    if not drives:
        for letter in "CDEFGH":
            p = letter + ":"
            if os.path.isdir(p + "\\"):
                drives.append(p)
    return drives


def _candidate_dirs(roots: List[str], deadline: float) -> List[str]:
    """把扫描根展开成一批"疑似游戏目录"。"""
    out: List[str] = []
    seen: Set[str] = set()

    for root in roots:
        if time.time() > deadline or len(out) >= MAX_CANDIDATES:
            break
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError:
            continue

        for entry in entries:
            if len(out) >= MAX_CANDIDATES or time.time() > deadline:
                break
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            low = entry.name.lower()
            if low in SKIP_DIR_NAMES or low in PLATFORM_DIR_NAMES or low.startswith("$"):
                continue

            if low in CONTAINER_NAMES:
                # 容器目录：它的子目录才是游戏
                try:
                    subs = sorted(os.scandir(entry.path), key=lambda e: e.name.lower())
                except OSError:
                    continue
                for sub in subs:
                    if len(out) >= MAX_CANDIDATES or time.time() > deadline:
                        break
                    try:
                        if not sub.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    sl = sub.name.lower()
                    if sl in SKIP_DIR_NAMES or sl in PLATFORM_DIR_NAMES:
                        continue
                    key = os.path.normcase(sub.path)
                    if key not in seen:
                        seen.add(key)
                        out.append(sub.path)
            else:
                key = os.path.normcase(entry.path)
                if key not in seen:
                    seen.add(key)
                    out.append(entry.path)

    return out


def _dir_size_capped(path: str, cap: int = BIG_DIR, max_dirs: int = 4000) -> int:
    """目录大小，超过 cap 立即返回（避免大目录上浪费时间）。"""
    total = 0
    visited = 0
    queue = [os.path.normpath(path)]
    while queue:
        d = queue.pop()
        visited += 1
        if visited > max_dirs:
            break
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            queue.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat().st_size
                            if total >= cap:
                                return total
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _name_has_release_tag(path: str) -> str:
    """目录名里是否带发布组 / 打包标记。"""
    low = os.path.basename(os.path.normpath(path)).lower()
    for tag in RELEASE_TAGS:
        if tag in low:
            return tag
    return ""


# 过于通用、不适合当游戏名的词
GENERIC_WORDS = {
    "app", "apps", "game", "games", "file", "files", "data", "bin", "build",
    "content", "program", "programs", "portable", "release", "main", "core",
    "client", "launcher", "start", "setup", "install", "download", "downloads",
    "x64", "x86", "win64", "win32", "windows", "steam", "pc", "my", "new",
    "temp", "tmp", "old", "backup", "copy", "test", "demo",
}

# 主程序名里常见的无意义后缀
_EXE_SUFFIX = re.compile(
    r"[._\-\s]*(?:win(?:32|64)?|x64|x86|dx1[12]|dx9|vk|vulkan|shipping|"
    r"client|game|launcher|steam|retail|release|final|main|app|"
    r"windows|desktop|gl|sd|hd)$", re.I)


def _best_name(path: str, exe: str) -> str:
    """从目录名 / 子目录名 / 主程序名里挑一个最像游戏名的。"""
    cands: List[str] = [os.path.basename(os.path.normpath(path))]
    if exe:
        exe_dir = os.path.dirname(exe)
        if os.path.normcase(exe_dir) != os.path.normcase(os.path.normpath(path)):
            cands.append(os.path.basename(exe_dir))
        stem = os.path.splitext(os.path.basename(exe))[0]
        # 反复剥离无意义后缀，如 EldenRing-Win64-Shipping -> EldenRing
        for _ in range(3):
            stripped = _EXE_SUFFIX.sub("", stem)
            if stripped == stem or len(stripped) < 3:
                break
            stem = stripped
        cands.append(stem)

    best, best_score = "", -999.0
    for c in cands:
        c = (c or "").strip()
        if not c:
            continue
        low = c.lower()
        score = 0.0
        tokens = re.split(r"[\s._\-\[\]（）()]+", low)
        if any(t in GENERIC_WORDS for t in tokens if t):
            score -= 3.0
        if low in GENERIC_WORDS:
            score -= 3.0
        if any(t in low for t in ("apps", "program files", "redist")):
            score -= 2.0
        if len(c) >= 4:
            score += 1.0
        if len(c) >= 8:
            score += 0.5
        # 首字母大写后接小写（Minecraft）最像作品名；开头连续大写（MCxxx）略减
        if re.match(r"^[A-Z][a-z]", c):
            score += 0.8
        if re.match(r"^[A-Z]{2,}", c):
            score -= 0.5
        # 含大小写混排或空格，更像作品名
        if re.search(r"[a-z][A-Z]", c) or " " in c:
            score += 1.0
        # 中文名对中文用户辨识度更高，给一点权重
        if re.search(r"[\u4e00-\u9fff]", c):
            score += 0.6
        # 纯 ASCII 无意义短串扣分
        if len(c) <= 3:
            score -= 1.5
        score += min(len(c), 24) / 24.0
        if score > best_score:
            best_score, best = score, c
    return best or os.path.basename(os.path.normpath(path))


# --------------------------------------------------------------------------
# 目录判定
# --------------------------------------------------------------------------

def _pick_exe(path: str) -> str:
    """在目录浅层里挑一个最像主程序的可执行文件（取体积最大的）。"""
    best = ""
    best_size = 0
    root = os.path.normpath(path)
    queue: List[Tuple[str, int]] = [(root, 0)]
    while queue:
        dirpath, depth = queue.pop(0)
        try:
            with os.scandir(dirpath) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < 2:
                                queue.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False) and e.name.lower().endswith(".exe"):
                            n = e.name.lower()
                            if any(t in n for t in BAD_EXE_TOKENS):
                                continue
                            size = e.stat().st_size
                            if size > best_size:
                                best_size, best = size, e.path
                    except OSError:
                        continue
        except OSError:
            continue
    return best


def _check_game_dir(path: str) -> Optional[dict]:
    """判定目录是否为游戏。是则返回描述信息，否则 None。

    采用高精度策略：宁可漏报，也不把普通软件误判成游戏。
    """
    try:
        engine, desc, hits = detect_engine(path)
    except Exception:
        return None

    engines = hits.get("Engine", [])
    emulators = hits.get("Emulator", [])
    markers = set(hits.get("Marker", []))
    evidences = hits.get("Evidence", [])

    strong_engine = next((e for e in engines if e in STRONG_ENGINES), "")
    strong_markers = sorted((markers & LOCAL_MARKERS) - WEAK_MARKERS)
    weak_markers = sorted(markers & WEAK_MARKERS)
    redist = bool(markers & REDIST_MARKERS)
    data_like = bool(markers & DATA_MARKERS)
    release_tag = _name_has_release_tag(path)

    exe = _pick_exe(path)
    if not exe:
        return None
    try:
        exe_size = os.path.getsize(exe)
    except OSError:
        exe_size = 0

    reasons: List[str] = []
    is_game = False

    # ---- 1. 引擎强信号：最可靠的判据 ----
    if strong_engine:
        is_game = True
        reasons.append(f"{strong_engine} 引擎")
    if emulators:
        is_game = True
        reasons.append(" / ".join(emulators) + " 模拟器")

    # ---- 2. 本地安装 / 发布组标记：离线安装游戏的典型特征 ----
    if strong_markers:
        is_game = True
        reasons.extend(strong_markers)
    if release_tag:
        is_game = True
        reasons.append(f"目录含「{release_tag}」标记")

    # ---- 3. 结构判定：必须同时满足「大目录 + 大主程序 + 游戏特征」----
    if not is_game and exe_size >= HUGE_EXE:
        size = _dir_size_capped(path)
        if size >= BIG_DIR and (redist or data_like):
            is_game = True
            reasons.append("超大目录")
            reasons.append("大体积主程序")
            if redist:
                reasons.append("含运行库")
            if data_like:
                reasons.append("含数据目录")

    # ---- 4. 弱标记需要强佐证 ----
    if not is_game and weak_markers and exe_size >= BIG_EXE:
        if redist or data_like:
            is_game = True
            reasons.extend(weak_markers)
            reasons.append("大体积主程序")

    if not is_game:
        return None

    # 判定为游戏后再算一次完整体积。
    # 上面结构判定里用的 _dir_size_capped(path) 是带 BIG_DIR 上限的
    # 快速判定（够大就收手），那个值不能用来展示 —— 用户要的是
    # 这个游戏在硬盘上真正占了多大，即整个目录的合计大小。
    dir_size = _dir_size_capped(path, cap=SIZE_CAP, max_dirs=8000)

    return {
        "engine": strong_engine,
        "desc": " · ".join(dict.fromkeys(reasons)),
        "exe": exe,
        "exe_size": exe_size,
        "size": dir_size,
        "markers": sorted(markers),
        "local": bool(strong_markers or weak_markers or release_tag),
    }


# --------------------------------------------------------------------------
# 卸载注册表
# --------------------------------------------------------------------------

_UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

_NOT_GAME_TOKENS = (
    "update", "redistributable", "runtime", "driver", "sdk", "toolkit",
    "framework", "python", "java", "visual studio", "office", "adobe",
    "browser", "chrome", "edge", "firefox", "winrar", "7-zip", "vlc",
    "antivirus", "defender", "driver", "printer", "audio", "chipset",
)


def _from_uninstall() -> List[dict]:
    """从卸载注册表里找出装在非标准位置的游戏。"""
    out: List[dict] = []
    seen: Set[str] = set()
    for hive, base in _UNINSTALL_KEYS:
        for sub in winutil.reg_subkeys_ex(hive, base, winutil.KEY_WOW64_32KEY):
            full = f"{base}\\{sub}"
            name = str(winutil.reg_read_ex(hive, full, "DisplayName",
                                           winutil.KEY_WOW64_32KEY) or "").strip()
            loc = str(winutil.reg_read_ex(hive, full, "InstallLocation",
                                          winutil.KEY_WOW64_32KEY) or "").strip()
            if not name or not loc:
                continue
            low = name.lower()
            if any(t in low for t in _NOT_GAME_TOKENS):
                continue
            loc = winutil.expand(loc).rstrip("\\/")
            if not os.path.isdir(loc):
                continue
            key = os.path.normcase(loc)
            if key in seen:
                continue
            seen.add(key)
            info = _check_game_dir(loc)
            if info:
                info["name"] = name
                info["path"] = loc
                out.append(info)
    return out


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def detect(extra_roots: Optional[List[str]] = None,
           progress=None) -> List[Game]:
    """扫描本地安装的游戏。

    extra_roots 为用户自定义的扫描目录（可在设置里添加）。
    """
    deadline = time.time() + TIME_BUDGET
    games: List[Game] = []
    seen: Set[str] = set()

    roots: List[str] = []
    for d in fixed_drives():
        roots.append(d + "\\")
    for extra in (extra_roots or []):
        if extra and os.path.isdir(extra):
            roots.append(extra)
    # 常见游戏目录兜底
    for d in fixed_drives():
        for sub in ("Games", "Game", "游戏", "PC Games", "My Games"):
            p = os.path.join(d + "\\", sub)
            if os.path.isdir(p):
                roots.append(p)

    candidates = _candidate_dirs(roots, deadline)

    total = len(candidates)
    for i, path in enumerate(candidates):
        if time.time() > deadline:
            break
        if progress:
            progress(os.path.basename(path), i, total)
        info = _check_game_dir(path)
        if not info:
            continue
        key = os.path.normcase(info["exe"])
        if key in seen:
            continue
        seen.add(key)
        games.append(Game(
            name=_best_name(path, info["exe"]),
            platform="local",
            launch_kind="exe",
            launch_target=info["exe"],
            install_dir=path,
            exe=info["exe"],
            size_bytes=info.get("size") or info["exe_size"],
            source=f"本地扫描 · {info['desc']}",
        ))

    # 卸载注册表补充（可能装在自定义路径）
    for info in _from_uninstall():
        key = os.path.normcase(info["exe"])
        if key in seen:
            continue
        seen.add(key)
        games.append(Game(
            name=info["name"],
            platform="local",
            launch_kind="exe",
            launch_target=info["exe"],
            install_dir=info["path"],
            exe=info["exe"],
            size_bytes=info.get("size") or info["exe_size"],
            source=f"卸载注册表 · {info['desc']}",
        ))

    return games
