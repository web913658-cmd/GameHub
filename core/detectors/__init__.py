"""游戏检测器包。

每个模块负责一个平台的已安装游戏探测，统一产出 core.models.Game。
检测逻辑参考了 GitHub 上的开源实现：
  - nexus-launcher (thebluedogcorp)  —— 各平台注册表/清单来源
  - WindowsGameExport (johnray)      —— 文件系统扫描与引擎特征
  - Playnite (JosefNemec)            —— 平台覆盖范围与启动协议
"""

from __future__ import annotations

from typing import Callable, List

from ..models import Game

from . import (steam, epic, gog, battlenet, ea, ubisoft, riot, xbox, amazon,
               local, standalone)

# 检测器注册表：(平台 key, 显示名, 检测函数)
DETECTORS: List[tuple] = [
    ("steam", "Steam", steam.detect),
    ("epic", "Epic Games", epic.detect),
    ("gog", "GOG Galaxy", gog.detect),
    ("battlenet", "Battle.net", battlenet.detect),
    ("ea", "EA App", ea.detect),
    ("ubisoft", "Ubisoft Connect", ubisoft.detect),
    ("riot", "Riot Client", riot.detect),
    ("xbox", "Xbox / Game Pass", xbox.detect),
    ("amazon", "Amazon Games", amazon.detect),
    ("local", "本地游戏", local.detect),
    ("standalone", "独立游戏", standalone.detect),
]

# 平台优先级（去重时保留优先级高的）
_PRIORITY = {k: i for i, (k, _, _) in enumerate(DETECTORS)}


def scan_all(progress: Callable[[str, int, int], None] | None = None,
             include_standalone: bool = True,
             include_local: bool = True,
             local_roots: list | None = None) -> List[Game]:
    """扫描所有平台，返回去重后的游戏列表。

    progress(platform_label, index, total) 用于向 UI 推送进度。
    """
    games: List[Game] = []
    active = []
    for entry in DETECTORS:
        key = entry[0]
        if key == "standalone" and not include_standalone:
            continue
        if key == "local" and not include_local:
            continue
        active.append(entry)

    total = len(active)
    for i, (key, label, fn) in enumerate(active):
        if progress:
            progress(label, i, total)
        try:
            if key == "local":
                found = fn(extra_roots=local_roots or []) or []
            else:
                found = fn() or []
        except Exception as exc:  # 单个平台失败不影响整体
            found = []
            print(f"[detector:{key}] 失败: {exc}")
        games.extend(found)

    if progress:
        progress("完成", total, total)

    return dedupe(games)


def dedupe(games: List[Game]) -> List[Game]:
    """按安装目录 / 名称去重，平台优先级高者保留。"""
    seen: dict = {}
    for g in games:
        # 用安装目录（规范化）或名称作为去重键
        key = (g.install_dir or g.name).strip().lower().replace("/", "\\").rstrip("\\")
        if not key:
            key = g.id
        prev = seen.get(key)
        if prev is None:
            seen[key] = g
        else:
            if _PRIORITY.get(g.platform, 99) < _PRIORITY.get(prev.platform, 99):
                seen[key] = g
    return sorted(seen.values(), key=lambda x: (x.name or "").lower())
