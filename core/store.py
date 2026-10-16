"""游戏库持久化。

库文件为 JSON，保存用户态数据（收藏、最近游玩、自定义名称、手动添加的游戏）。
重新扫描时按 id 合并，保留用户改动。
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from .models import Game

DEFAULT_CONFIG = {
    "steamgriddb_key": "",
    "include_standalone": True,
    "include_local": True,
    "local_roots": [],
    "scan_roots": [],
    "theme": "ios",
    "sort": "name",
}


class Library:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.games_path = os.path.join(data_dir, "library.json")
        self.config_path = os.path.join(data_dir, "config.json")
        self.games: List[Game] = []
        self.config: dict = dict(DEFAULT_CONFIG)
        self._manual: List[dict] = []       # 手动添加、不参与自动扫描
        self.last_scan: int = 0
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        try:
            if os.path.isfile(self.games_path):
                with open(self.games_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.games = [Game.from_dict(d) for d in data.get("games", [])]
                self._manual = data.get("manual", [])
                self.last_scan = data.get("last_scan", 0)
        except Exception:
            self.games = []
        try:
            if os.path.isfile(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
        except Exception:
            pass

    def save(self) -> None:
        try:
            with open(self.games_path, "w", encoding="utf-8") as f:
                json.dump({
                    "games": [g.to_dict() for g in self.games],
                    "manual": self._manual,
                    "last_scan": self.last_scan,
                }, f, ensure_ascii=False, indent=1)
        except Exception as exc:
            print(f"[library] 保存失败: {exc}")

    def save_config(self) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def merge_scan(self, scanned: List[Game]) -> List[Game]:
        """把新扫描结果与已有库合并，保留用户态字段。"""
        old = {g.id: g for g in self.games}
        merged: List[Game] = []
        for g in scanned:
            prev = old.get(g.id)
            if prev:
                g.favorite = prev.favorite
                g.last_played = prev.last_played
                g.play_count = prev.play_count
                if prev.name and prev.name != g.name:
                    pass  # 以扫描结果为准
            merged.append(g)

        # 手动添加的游戏始终保留
        for d in self._manual:
            try:
                m = Game.from_dict(d)
            except Exception:
                continue
            if not any(x.id == m.id for x in merged):
                merged.append(m)

        self.games = merged
        self.last_scan = int(time.time())
        self.save()
        return merged

    def get(self, gid: str) -> Optional[Game]:
        for g in self.games:
            if g.id == gid:
                return g
        return None

    def add_manual(self, name: str, target: str, platform: str = "standalone") -> Game:
        g = Game(
            name=name,
            platform=platform,
            launch_kind="exe" if target.lower().endswith(".exe") else "protocol",
            launch_target=target,
            install_dir=os.path.dirname(target) if os.path.isfile(target) else "",
            exe=target if target.lower().endswith(".exe") else "",
            source="手动添加",
        )
        self.games.append(g)
        self._manual.append(g.to_dict())
        self.save()
        return g

    def remove(self, gid: str) -> bool:
        before = len(self.games)
        self.games = [g for g in self.games if g.id != gid]
        self._manual = [d for d in self._manual if d.get("id") != gid]
        self.save()
        return len(self.games) < before
