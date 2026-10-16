"""数据模型定义。

Game 是整个应用的核心数据结构，所有平台的检测器最终都产出 Game 对象。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


# 平台标识 -> 中文展示名
PLATFORM_LABELS = {
    "steam": "Steam",
    "epic": "Epic Games",
    "gog": "GOG Galaxy",
    "battlenet": "Battle.net",
    "ea": "EA App",
    "ubisoft": "Ubisoft Connect",
    "riot": "Riot Client",
    "xbox": "Xbox / Game Pass",
    "amazon": "Amazon Games",
    "local": "本地游戏",
    "standalone": "独立游戏",
}

# 各平台品牌色（用于前端角标）
PLATFORM_COLORS = {
    "steam": "#1b2838",
    "epic": "#2a2a2a",
    "gog": "#5a3f9d",
    "battlenet": "#148eff",
    "ea": "#ff4747",
    "ubisoft": "#0070ff",
    "riot": "#d13639",
    "xbox": "#107c10",
    "amazon": "#ff9900",
    "local": "#5856d6",
    "standalone": "#8e8e93",
}


@dataclass
class Game:
    """一个已安装游戏。"""

    name: str
    platform: str                     # 见 PLATFORM_LABELS
    launch_kind: str                  # protocol | exe | shell | uri
    launch_target: str                # 协议 URI / 可执行文件路径 / shell 目标
    install_dir: str = ""
    app_id: str = ""                  # 平台内唯一 ID（Steam appid / Epic AppName 等）
    exe: str = ""                     # 主可执行文件（若可知）
    args: str = ""
    cover: str = ""                   # 竖版海报（600x900）
    hero: str = ""                    # 横版大图
    logo: str = ""                    # 透明 Logo
    icon: str = ""                    # 小图标
    size_bytes: int = 0
    last_played: int = 0              # unix 时间戳
    play_count: int = 0
    favorite: bool = False
    source: str = ""                  # 检测来源说明（调试用）

    # 运行期字段（不持久化到库文件里也保留，方便前端直接用）
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.make_id()

    def make_id(self) -> str:
        """稳定 ID：平台 + 应用 ID + 名称，保证多次扫描结果可对齐。"""
        raw = f"{self.platform}|{self.app_id}|{self.name}|{self.launch_target}".lower()
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Game":
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})
