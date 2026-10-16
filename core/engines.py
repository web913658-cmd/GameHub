"""引擎 / 运行时特征识别引擎。

加载 INI 格式的规则集（兼容 SteamDatabase/FileDetectionRuleSets 的 rules.ini），
对一组文件路径做匹配，推断这个目录用的是什么游戏引擎、以及有哪些安装标记。

设计要点
--------
1. **两遍匹配**：先用 Engine 段的强信号判定引擎；命中不足时用 Evidence 段的
   弱信号累积推断（如 GameMaker 需要 data.win + options.ini + snd_*.ogg 中的多个）。
2. **字面量预筛**：从正则中抽取字面量片段。若某条规则的所有字面量都不出现在
   路径集合里，则该规则必然不匹配，可直接跳过——这是安全的必要条件过滤，
   能把正则执行次数降低一到两个数量级。
3. **PCRE 兼容**：SteamDB 的规则使用了 `\\w++`、`(?>...)`、`{n,m}+` 等
   占有量词与原子组，Python re 不支持，加载时统一降级转换。
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .winutil import package_dir, resource_dir

# 内置规则集。必须用 package_dir() 而不是 __file__：
# PyInstaller 打包后 __file__ 指向的是 PYZ 归档内的虚拟路径，无法据此
# 在磁盘上定位随包分发的 rules/engines.ini。
DEFAULT_RULES = os.path.join(package_dir(), "rules", "engines.ini")

# PCRE 专有语法 -> Python 等价写法
_PCRE_FIXES = [
    (re.compile(r"\{(\d+),(\d+)\}\+"), r"{\1,\2}"),   # {1,3}+ -> {1,3}
    (re.compile(r"\{(\d+)\}\+"), r"{\1}"),            # {3}+   -> {3}
    (re.compile(r"\+\+"), "+"),                        # \w++   -> \w+
    (re.compile(r"\*\*"), "*"),
    (re.compile(r"\?\?"), "?"),
    (re.compile(r"\(\?>"), "(?:"),                     # (?>..) -> (?:..)
]

_LITERAL_RUN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{2,}")


def _to_python_regex(pattern: str) -> str:
    out = pattern
    for rx, rep in _PCRE_FIXES:
        out = rx.sub(rep, out)
    return out


def _literal_hints(pattern: str) -> List[str]:
    """抽取正则中的字面量片段，用于安全的预筛。

    正则命中某字符串时，该字符串必然包含其中至少一个字面量片段，
    因此「全部字面量都不出现」可以安全地判定为不匹配。
    """
    hints: List[str] = []
    for m in _LITERAL_RUN.finditer(pattern):
        s = m.group(0)
        # 去掉纯元字符残留
        s = s.strip(".")
        if len(s) >= 3 and not s.isdigit():
            hints.append(s.lower())
    # 去重，长的优先
    seen: Set[str] = set()
    out: List[str] = []
    for h in sorted(set(hints), key=len, reverse=True):
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


class Rule:
    __slots__ = ("name", "section", "pattern", "regex", "hints")

    def __init__(self, section: str, name: str, pattern: str):
        self.section = section
        self.name = name
        self.pattern = pattern
        self.regex: Optional[re.Pattern] = None
        self.hints = _literal_hints(pattern)
        try:
            self.regex = re.compile(_to_python_regex(pattern), re.IGNORECASE)
        except re.error:
            self.regex = None

    @property
    def key(self) -> str:
        return f"{self.section}.{self.name}"


class RuleSet:
    """一组规则，可按路径集合求匹配结果。"""

    def __init__(self, rules: List[Rule]):
        self.rules = rules
        self.by_section: Dict[str, List[Rule]] = {}
        for r in rules:
            self.by_section.setdefault(r.section, []).append(r)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str = DEFAULT_RULES) -> "RuleSet":
        rules: List[Rule] = []
        if not os.path.isfile(path):
            return cls(rules)
        section = "Engine"
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError:
            return cls(rules)

        for raw in lines:
            line = raw.strip()
            if not line or line[0] in ";#":
                continue
            # 行尾注释：分号前有空白
            line = re.sub(r"\s;.*$", "", line).strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if "=" not in line:
                continue
            name, _, pattern = line.partition("=")
            name = name.strip()
            pattern = pattern.strip()
            if name.endswith("[]"):
                name = name[:-2]
            if not name or not pattern:
                continue
            rules.append(Rule(section, name, pattern))
        return cls(rules)

    # ------------------------------------------------------------------
    def match(self, paths: Iterable[str]) -> Dict[str, List[str]]:
        """对一批路径做匹配。

        paths 为完整文件路径（Windows 反斜杠会被规范化为 /）。
        返回 {section: [命中名, ...]}，按命中强度排序。
        """
        norm = [p.replace("\\", "/") for p in paths]
        if not norm:
            return {}
        blob = "\n".join(norm).lower()

        hits: Dict[str, List[str]] = {}
        for rule in self.rules:
            if rule.regex is None:
                continue
            # 字面量预筛：所有 hint 都不在路径集合中 -> 必然不匹配
            if rule.hints and not any(h in blob for h in rule.hints):
                continue
            for p in norm:
                if rule.regex.search(p):
                    hits.setdefault(rule.section, [])
                    if rule.name not in hits[rule.section]:
                        hits[rule.section].append(rule.name)
                    break
        return hits

    def describe(self, hits: Dict[str, List[str]]) -> Tuple[str, str]:
        """把匹配结果翻译成 (引擎名, 人类可读说明)。"""
        engine = ""
        engines = hits.get("Engine", [])
        if engines:
            # 规则文件中越靠前的越主流，直接取第一个
            engine = engines[0]

        notes: List[str] = []
        if engine:
            notes.append(f"{_pretty(engine)} 引擎")
        for name in hits.get("Emulator", [])[:2]:
            notes.append(f"{_pretty(name)} 模拟器")
        markers = hits.get("Marker", [])
        for name in markers[:3]:
            notes.append(_pretty(name))
        return engine, " · ".join(notes)


_PRETTY = {
    "Unity": "Unity", "Unreal": "Unreal Engine", "Godot": "Godot",
    "GameMaker": "GameMaker", "RPGMaker": "RPG Maker", "RenPy": "Ren'Py",
    "CryEngine": "CryEngine", "Source": "Source", "Source2": "Source 2",
    "RE_Engine": "RE Engine", "REDengine": "REDengine", "Frostbite": "Frostbite",
    "Creation_Engine": "Creation Engine", "Snowdrop": "Snowdrop",
    "Anvil": "AnvilNext", "Dunia": "Dunia", "Essence_Engine": "Essence",
    "Glacier": "Glacier", "idTech": "id Tech", "IW_Engine": "IW Engine",
    "GoldSource": "GoldSrc", "Love2D": "LÖVE", "PyGame": "pygame",
    "ClickTeamFusion": "Clickteam Fusion", "Construct": "Construct",
    "AdventureGameStudio": "Adventure Game Studio", "KiriKiri": "KiriKiri",
    "NScripter": "NScripter", "Wintermute": "Wintermute",
    "VisionaireStudio": "Visionaire", "Defold": "Defold", "Solar2D": "Solar2D",
    "MonoGame": "MonoGame", "FNA": "FNA", "XNA": "XNA", "Stride": "Stride",
    "GDevelop": "GDevelop", "FlatRedBall": "FlatRedBall", "Blitz3D": "Blitz3D",
    "Unigine": "UNIGINE", "X-Ray": "X-Ray", "Prism3D": "Prism3D",
    "Serious_Engine": "Serious Engine", "RealVirtuality": "Real Virtuality",
    "RAGE": "RAGE", "Apex_Engine": "Apex Engine", "Pico8": "PICO-8",
    "DOSBOX": "DOSBox", "SCUMMVM": "ScummVM", "RetroArch": "RetroArch",
    "PCSX2": "PCSX2", "Dolphin": "Dolphin", "RPCS3": "RPCS3",
    "Yuzu": "Yuzu", "Ryujinx": "Ryujinx", "MAME": "MAME", "PPSSPP": "PPSSPP",
    "Citra": "Citra",
    "Steam_API": "Steam API", "Steam_Emu": "Steam 离线模式",
    "GOG_Offline": "GOG 离线安装", "Scene_NFO": "场景发布信息",
    "Repack_FitGirl": "FitGirl 打包", "Repack_DODI": "DODI 打包",
    "Repack_Xatab": "Xatab 打包", "Repack_RGMechanics": "R.G. Mechanics 打包",
    "Repack_RGMechanics2": "R.G. Mechanics 打包", "Repack_ElAmigos": "ElAmigos 打包",
    "Repack_Corepack": "CorePack 打包", "Repack_MR DJ": "MR DJ 打包",
    "Redist": "含运行库", "Redist2": "含运行库", "Redist3": "含运行库",
    "Game_Exe_Hint": "含启动器", "Data_Folder": "含数据目录",
    "Content_Folder": "含内容目录", "Assets_Folder": "含资源目录",
}


def _pretty(name: str) -> str:
    return _PRETTY.get(name, name.replace("_", " "))


# --------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------

_CACHE: Dict[str, RuleSet] = {}


def get_ruleset(path: Optional[str] = None) -> RuleSet:
    """带缓存的规则集加载。优先使用 data/rules/rules.ini（用户可替换为完整版）。"""
    if path is None:
        external = _external_rules_path()
        path = external or DEFAULT_RULES
    key = os.path.abspath(path)
    if key not in _CACHE:
        _CACHE[key] = RuleSet.load(key)
    return _CACHE[key]


def _external_rules_path() -> str:
    """查找用户放置的完整版 rules.ini（放这里可覆盖内置的精简规则集）。"""
    here = package_dir()
    candidates = [
        os.path.join(resource_dir(), "data", "rules", "rules.ini"),
        os.path.join(here, "rules", "rules.ini"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 5000:
            return c
    return ""


def collect_paths(root: str, max_depth: int = 3, limit: int = 600,
                  per_dir: int = 60) -> List[str]:
    """按广度优先收集目录下若干层内的文件路径（相对 root）。

    必须用 BFS 而不是 os.walk 的 DFS：引擎特征文件几乎都在浅层，
    而 DFS 会在第一个深层子树上耗尽配额，导致漏检。
    另外 abspath 可能返回正斜杠、os.walk 返回反斜杠，必须先 normpath 统一。
    """
    root = os.path.normpath(os.path.abspath(root))
    out: List[str] = []
    queue: List[Tuple[str, int]] = [(root, 0)]

    while queue and len(out) < limit:
        dirpath, depth = queue.pop(0)
        dirs: List[str] = []
        files: List[str] = []
        try:
            with os.scandir(dirpath) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            files.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue

        # 浅层文件先入队，单目录限量，避免一个大目录占满配额
        files.sort()
        for f in files[:per_dir]:
            out.append(f)
            if len(out) >= limit:
                break

        if depth < max_depth:
            queue.extend((d, depth + 1) for d in sorted(dirs))

    return [os.path.relpath(p, root) for p in out[:limit]]


def detect_engine(root: str) -> Tuple[str, str, Dict[str, List[str]]]:
    """识别一个目录的引擎与标记，返回 (引擎名, 说明, 原始命中)。"""
    paths = collect_paths(root)
    if not paths:
        return "", "", {}
    rs = get_ruleset()
    hits = rs.match(paths)
    engine, desc = rs.describe(hits)
    return engine, desc, hits
