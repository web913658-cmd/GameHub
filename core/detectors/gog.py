"""GOG Galaxy 检测。

注册表：HKLM\\SOFTWARE\\WOW6432Node\\GOG.com\\Games\\<id>
  值：gameName / path / exe / launchCommand / gameID / workingDir
兜底：游戏目录下的 goggame-*.info (JSON)
启动：gog://<id> 或直接运行 exe
"""

from __future__ import annotations

import glob
import json
import os
from typing import List

from ..models import Game
from .. import winutil

REG_BASES = [
    r"SOFTWARE\WOW6432Node\GOG.com\Games",
    r"SOFTWARE\GOG.com\Games",
]


def detect() -> List[Game]:
    games: List[Game] = []
    seen_ids = set()

    for base in REG_BASES:
        for sub in winutil.reg_subkeys_ex("HKLM", base, winutil.KEY_WOW64_32KEY):
            full = f"{base}\\{sub}"
            vals = winutil.reg_values("HKLM", full)
            if not vals:
                continue
            name = str(vals.get("gameName") or vals.get("GAME_NAME") or "").strip()
            if not name:
                continue
            install = winutil.expand(str(vals.get("path") or vals.get("PATH") or ""))
            exe = winutil.expand(str(vals.get("exe") or vals.get("EXE") or ""))
            game_id = str(vals.get("gameID") or vals.get("GAME_ID") or sub).strip()
            if game_id in seen_ids:
                continue
            seen_ids.add(game_id)

            exe_path = os.path.join(install, exe.replace("/", "\\")) if (install and exe) else ""
            if exe_path and not os.path.isfile(exe_path):
                exe_path = ""

            games.append(Game(
                name=name,
                platform="gog",
                launch_kind="protocol" if game_id else "exe",
                launch_target=f"gog://{game_id}" if game_id else exe_path,
                install_dir=install,
                app_id=game_id,
                exe=exe_path,
                source=f"registry @ {full}",
            ))

    # 兜底：扫描常见 GOG 目录里的 goggame-*.info
    for base in (r"C:\GOG Games", r"D:\GOG Games", r"C:\Program Files (x86)\GOG Galaxy\Games"):
        if not os.path.isdir(base):
            continue
        for info in glob.glob(os.path.join(base, "*", "goggame-*.info")):
            try:
                with open(info, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
            except Exception:
                continue
            name = (data.get("name") or "").strip()
            gid = str(data.get("gameId") or "").strip()
            if not name or gid in seen_ids:
                continue
            seen_ids.add(gid)
            install = os.path.dirname(info)
            play = data.get("playTasks") or []
            exe = ""
            for t in play:
                if isinstance(t, dict) and t.get("type") == "FileTask" and t.get("path"):
                    exe = os.path.join(install, str(t["path"]).replace("/", "\\"))
                    break
            games.append(Game(
                name=name,
                platform="gog",
                launch_kind="protocol" if gid else "exe",
                launch_target=f"gog://{gid}" if gid else exe,
                install_dir=install,
                app_id=gid,
                exe=exe,
                source=f"goggame info @ {info}",
            ))
    return games
