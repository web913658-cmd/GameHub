"""Steam 全量应用列表（离线名称 → appid 匹配）。

用途：当 Steam 商店搜索接口不可达时，仍能把游戏名解析成 appid，
从而拿到官方海报。这是 SteamDB 一类工具的常规做法。

数据来源：https://api.steampowered.com/ISteamApps/GetAppList/v2/
    返回 {"applist": {"apps": [{"appid": 440, "name": "Team Fortress 2"}, ...]}}

设计要点：
  · **懒加载**：只在在线接口失败、且确实需要时才读入内存，
    正常运行完全不占内存，也不会有意料之外的十几 MB 下载。
  · **优雅降级**：下载失败（被墙、超时）就标记不可用，不影响主流程。
  · 缓存为 gzip JSON，体积约 2 MB。
"""

from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

GET_APP_LIST = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
CACHE_NAME = "steam_applist.json.gz"
MAX_AGE = 7 * 86400          # 7 天后视为过期
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GameHub/1.0"

_TRADEMARK = re.compile(r"[™®©]")
_NOISE = re.compile(
    r"\b(edition|definitive|deluxe|ultimate|complete|remastered|remake|goty|"
    r"game of the year|standard|premium|gold|enhanced|hd|vr)\b", re.I)


def _norm(name: str) -> str:
    s = _TRADEMARK.sub("", name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = _NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    """与 artwork._similarity 保持一致：覆盖率 + 前缀加成 ± 多余词处理。

    多余词在查询词之前视为发布商前缀（同一款游戏），在之后视为实质区分（不同游戏）。
    """
    na, nb = _norm(a), _norm(b)
    ta_list, tb_list = na.split(), nb.split()
    ta, tb = set(ta_list), set(tb_list)
    if not ta or not tb:
        return 0.0
    score = len(ta & tb) / max(len(ta), len(tb))
    if na and nb and (na.startswith(nb) or nb.startswith(na)):
        score += 0.25
    extra = tb - ta
    if extra:
        first_q = next((i for i, t in enumerate(tb_list) if t in ta), len(tb_list))
        if set(tb_list[:first_q]) >= extra:
            score += 0.15
        else:
            score -= 0.3 * len(extra)
    return score


class AppList:
    """本地 appid 全量表。"""

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, CACHE_NAME)
        self._entries: List[Tuple[int, str]] = []      # [(appid, name)]
        self._names_lower: List[str] = []              # 与 entries 一一对应
        self._index: Dict[str, int] = {}               # 归一化名 -> appid
        self._loaded = False
        self._error = ""
        self.last_error = ""

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return os.path.isfile(self.path)

    def status(self) -> dict:
        info = {"available": self.available, "count": len(self._entries),
                "loaded": self._loaded, "age_days": 0, "size_mb": 0,
                "error": self.last_error}
        if self.available:
            try:
                info["age_days"] = round((time.time() - os.path.getmtime(self.path)) / 86400, 1)
                info["size_mb"] = round(os.path.getsize(self.path) / 1048576, 2)
            except OSError:
                pass
        if not info["count"] and self.available:
            info["count"] = -1          # 已缓存但尚未加载
        return info

    def needs_refresh(self) -> bool:
        if not self.available:
            return True
        try:
            return (time.time() - os.path.getmtime(self.path)) > MAX_AGE
        except OSError:
            return True

    # ------------------------------------------------------------------
    # 下载 / 缓存
    # ------------------------------------------------------------------
    def download(self, timeout: int = 120, progress=None) -> Tuple[bool, str]:
        """下载并缓存全量表。返回 (是否成功, 说明)。"""
        if progress:
            progress("正在下载 Steam 全量应用列表…")
        try:
            req = urllib.request.Request(GET_APP_LIST, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False, f"下载失败（{type(exc).__name__}），已跳过离线匹配"

        try:
            data = json.loads(raw.decode("utf-8", "ignore"))
            apps = data["applist"]["apps"]
        except Exception as exc:
            self.last_error = f"解析失败: {exc}"
            return False, "响应格式异常，已跳过离线匹配"

        # 只保留有名字的条目，压缩存储
        compact = [[int(a.get("appid", 0)), str(a.get("name", ""))]
                   for a in apps if a.get("name")]
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with gzip.open(self.path, "wt", encoding="utf-8") as f:
                json.dump(compact, f, ensure_ascii=False)
        except OSError as exc:
            self.last_error = f"写入失败: {exc}"
            return False, "缓存写入失败"

        self.last_error = ""
        self._loaded = False            # 下次访问重新加载
        return True, f"已缓存 {len(compact):,} 条应用记录"

    # ------------------------------------------------------------------
    # 加载与匹配
    # ------------------------------------------------------------------
    def _load(self) -> bool:
        if self._loaded:
            return True
        if not self.available:
            return False
        try:
            with gzip.open(self.path, "rt", encoding="utf-8") as f:
                compact = json.load(f)
        except Exception as exc:
            self.last_error = f"读取缓存失败: {exc}"
            return False

        entries: List[Tuple[int, str]] = []
        names_lower: List[str] = []
        index: Dict[str, int] = {}
        for pair in compact:
            try:
                appid, name = int(pair[0]), str(pair[1])
            except (IndexError, TypeError, ValueError):
                continue
            if not name or appid <= 0:
                continue
            entries.append((appid, name))
            names_lower.append(name.lower())
            n = _norm(name)
            # 重名时保留 appid 较小的（通常是本体而非 DLC）
            if n and (n not in index or appid < index[n]):
                index[n] = appid

        self._entries = entries
        self._names_lower = names_lower
        self._index = index
        self._loaded = True
        return True

    def lookup(self, name: str) -> str:
        """按名称查 appid，命中返回字符串，否则空串。"""
        if not name or not self._load():
            return ""

        # 1) 归一化后精确命中
        n = _norm(name)
        if n and n in self._index:
            return str(self._index[n])

        # 2) 子串筛选 + 相似度（子串检查是 C 级操作，20 万条只需几十毫秒）
        needle = n or name.lower()
        if len(needle) < 3:
            return ""
        candidates: List[Tuple[float, int]] = []
        for i, low in enumerate(self._names_lower):
            if needle in low or low in needle:
                appid, orig = self._entries[i]
                s = _similarity(name, orig)
                if s >= 0.5:
                    candidates.append((s, appid))
        if not candidates:
            return ""
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return str(candidates[0][1])


# --------------------------------------------------------------------------
# 模块级单例
# --------------------------------------------------------------------------

_INSTANCE: Optional[AppList] = None


def get(data_dir: str) -> AppList:
    global _INSTANCE
    if _INSTANCE is None or _INSTANCE.path != os.path.join(data_dir, CACHE_NAME):
        _INSTANCE = AppList(data_dir)
    return _INSTANCE


def lookup(name: str, data_dir: str = "") -> str:
    """便捷入口：查询本地全量表。未下载或未加载则返回空串。"""
    inst = _INSTANCE if data_dir == "" else get(data_dir)
    if inst is None:
        return ""
    return inst.lookup(name)
