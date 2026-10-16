"""Valve Data Format (VDF) 解析器。

Steam 的 libraryfolders.vdf 与 appmanifest_*.acf 都是 VDF 格式。
这里实现一个无依赖的递归下降解析器，输出为嵌套 dict。

参考实现思路：nexus-launcher / WindowsGameExport 的 VDF 解析逻辑。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


_TOKEN_RE = re.compile(
    r"""
    (?P<comment>//[^\n]*)
  | (?P<brace>[{}])
  | (?P<string>"(?:\\.|[^"\\])*")
  | (?P<bare>[^\s{}"]+)
    """,
    re.VERBOSE,
)


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for m in _TOKEN_RE.finditer(text):
        if m.lastgroup == "comment":
            continue
        if m.lastgroup == "string":
            raw = m.group("string")[1:-1]
            # 处理 \" 与 \\ 转义
            raw = raw.replace('\\"', '"').replace("\\\\", "\\")
            tokens.append(raw)
        else:
            tokens.append(m.group(0))
    return tokens


def parse(text: str) -> Dict[str, Any]:
    """解析 VDF 文本，返回 dict。"""
    tokens = _tokenize(text)
    pos = 0

    def parse_block(stop_at_brace: bool) -> Dict[str, Any]:
        nonlocal pos
        obj: Dict[str, Any] = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                if stop_at_brace:
                    pos += 1
                    return obj
                pos += 1
                continue
            if tok == "{":
                pos += 1
                continue
            key = tok
            pos += 1
            if pos >= len(tokens):
                break
            nxt = tokens[pos]
            if nxt == "{":
                pos += 1
                obj[key] = parse_block(True)
            else:
                obj[key] = nxt
                pos += 1
        return obj

    return parse_block(False)


def parse_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return parse(f.read())
