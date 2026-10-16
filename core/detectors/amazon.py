"""Amazon Games 检测。

数据库：%LOCALAPPDATA%\\Amazon Games\\Data\\Games\\Sql\\GameInstallInfo.sqlite
"""

from __future__ import annotations

import os
import sqlite3
from typing import List

from ..models import Game

DB_PATHS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Amazon Games\Data\Games\Sql\GameInstallInfo.sqlite"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Amazon Games\Data\Games\Sql\GameInstallInfo.db"),
]


def detect() -> List[Game]:
    games: List[Game] = []
    for db in DB_PATHS:
        if not os.path.isfile(db):
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            for tbl in tables:
                cur.execute(f"PRAGMA table_info('{tbl}')")
                cols = [r[1] for r in cur.fetchall()]
                low = {c.lower(): c for c in cols}
                name_col = next((low[c] for c in low if "name" in c or "title" in c), None)
                path_col = next((low[c] for c in low if "path" in c or "dir" in c), None)
                exe_col = next((low[c] for c in low if c in ("exe", "executable", "launch")), None)
                if not name_col or not path_col:
                    continue
                cur.execute(f"SELECT * FROM '{tbl}'")
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    name = str(d.get(name_col) or "").strip()
                    install = str(d.get(path_col) or "").strip()
                    if not name or not install or not os.path.isdir(install):
                        continue
                    exe = str(d.get(exe_col) or "").strip() if exe_col else ""
                    exe_path = os.path.join(install, exe) if exe else ""
                    games.append(Game(
                        name=name,
                        platform="amazon",
                        launch_kind="exe" if exe_path else "protocol",
                        launch_target=exe_path or f"amazon-games://launch?titleId={name}",
                        install_dir=install,
                        app_id=str(d.get("Id") or name),
                        exe=exe_path,
                        source=f"sqlite @ {db}",
                    ))
            conn.close()
        except Exception:
            continue
    # 去重
    seen = {}
    for g in games:
        seen.setdefault(g.install_dir.lower(), g)
    return list(seen.values())
