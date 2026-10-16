"""Battle.net / 暴雪游戏检测。

注册表：HKLM\\SOFTWARE\\WOW6432Node\\Blizzard Entertainment\\<Product>
  值：InstallLocation
数据库：C:\\ProgramData\\Battle.net\\Agent\\product.db  (SQLite)
启动：battlenet://<product_code>
"""

from __future__ import annotations

import os
import sqlite3
from typing import List

from ..models import Game
from .. import winutil

# 产品代号 -> 友好名（注册表键名本身就是代号）
PRODUCT_NAMES = {
    "WoW": "World of Warcraft",
    "WTCG": "Hearthstone",
    "S2": "StarCraft II",
    "D3": "Diablo III",
    "D4": "Diablo IV",
    "Hero": "Heroes of the Storm",
    "Pro": "Overwatch 2",
    "VIPR": "Call of Duty: Modern Warfare",
    "ODIN": "Call of Duty: Warzone",
    "LAZR": "Call of Duty: Black Ops Cold War",
    "ANBS": "Diablo Immortal",
    "W3": "Warcraft III: Reforged",
    "RTRO": "Warcraft Rumble",
    "AUKS": "Crash Bandicoot 4",
    "FORE": "Call of Duty: Vanguard",
}

PRODUCT_DB = r"C:\ProgramData\Battle.net\Agent\product.db"


def _from_registry() -> List[Game]:
    games: List[Game] = []
    for base in (r"SOFTWARE\WOW6432Node\Blizzard Entertainment",
                 r"SOFTWARE\Blizzard Entertainment"):
        for sub in winutil.reg_subkeys_ex("HKLM", base, winutil.KEY_WOW64_32KEY):
            full = f"{base}\\{sub}"
            install = winutil.reg_read_ex("HKLM", full, "InstallLocation",
                                          winutil.KEY_WOW64_32KEY)
            if not install:
                install = winutil.reg_read_ex("HKLM", full, "", winutil.KEY_WOW64_32KEY)
            install = winutil.expand(str(install or ""))
            if not install or not os.path.isdir(install):
                continue
            code = sub
            name = PRODUCT_NAMES.get(code, code)
            games.append(Game(
                name=name,
                platform="battlenet",
                launch_kind="protocol",
                launch_target=f"battlenet://{code}",
                install_dir=install,
                app_id=code,
                source=f"registry @ {full}",
            ))
    return games


def _from_db() -> List[Game]:
    if not os.path.isfile(PRODUCT_DB):
        return []
    games: List[Game] = []
    try:
        conn = sqlite3.connect(f"file:{PRODUCT_DB}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        if "products" not in tables:
            conn.close()
            return []
        cur.execute("PRAGMA table_info(products)")
        cols = [r[1] for r in cur.fetchall()]
        cur.execute("SELECT * FROM products")
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            code = str(d.get("product_code") or d.get("uid") or d.get("name") or "").strip()
            install = winutil.expand(str(d.get("install_path") or d.get("settings") or ""))
            if not code:
                continue
            games.append(Game(
                name=PRODUCT_NAMES.get(code, code),
                platform="battlenet",
                launch_kind="protocol",
                launch_target=f"battlenet://{code}",
                install_dir=install if os.path.isdir(install) else "",
                app_id=code,
                source="product.db",
            ))
        conn.close()
    except Exception:
        return []
    return games


def detect() -> List[Game]:
    merged: dict = {}
    for g in _from_registry() + _from_db():
        merged.setdefault(g.app_id, g)
    return list(merged.values())
