"""游戏启动器。

按 launch_kind 分派：
  protocol -> os.startfile(uri)             走各平台注册的 URI 协议
  exe      -> subprocess.Popen([exe])       直接运行可执行文件
  shell    -> explorer.exe shell:AppsFolder 启动 UWP / Xbox 游戏
  uri      -> 同 protocol
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Tuple

from .models import Game


def launch(game: Game) -> Tuple[bool, str]:
    """启动游戏，返回 (是否成功, 提示信息)。"""
    kind = game.launch_kind
    target = game.launch_target

    if not target:
        return False, "缺少启动目标"

    try:
        if kind in ("protocol", "uri"):
            os.startfile(target)  # type: ignore[attr-defined]
            return True, f"已通过协议启动：{target}"

        if kind == "shell":
            subprocess.Popen(
                ["explorer.exe", target],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True, f"已通过 shell 启动：{target}"

        if kind == "exe":
            exe = target
            if not os.path.isfile(exe):
                return False, f"可执行文件不存在：{exe}"
            cwd = game.install_dir if game.install_dir and os.path.isdir(game.install_dir) \
                else os.path.dirname(exe)
            args = [exe]
            if game.args:
                args += game.args.split()
            subprocess.Popen(args, cwd=cwd, close_fds=True)
            return True, f"已启动：{os.path.basename(exe)}"

        return False, f"未知启动类型：{kind}"
    except Exception as exc:
        return False, f"启动失败：{exc}"


def mark_played(game: Game) -> None:
    game.last_played = int(time.time())
    game.play_count = (game.play_count or 0) + 1


def open_folder(path: str) -> Tuple[bool, str]:
    """在资源管理器中打开安装目录。"""
    if not path or not os.path.isdir(path):
        return False, "目录不存在"
    try:
        subprocess.Popen(["explorer.exe", os.path.normpath(path)])
        return True, "已打开目录"
    except Exception as exc:
        return False, str(exc)
