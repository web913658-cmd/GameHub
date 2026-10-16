"""从 PE 可执行文件中提取原始图标（纯标准库，无外部依赖）。

Windows 的 System.Drawing.Icon.ExtractAssociatedIcon 只能拿到 32×32，
放大到卡片上会明显发糊。这里直接解析 PE 资源段里的
RT_GROUP_ICON / RT_ICON，取出程序自带的最高分辨率图标（通常 256×256）。

只读取文件，不做任何修改。
"""

from __future__ import annotations

import os
import struct
from typing import Dict, List, Optional, Tuple

RT_ICON = 3
RT_GROUP_ICON = 14


def _rva_to_offset(sections: List[Tuple[int, int, int, int]], rva: int) -> Optional[int]:
    """把 RVA 换算成文件偏移。sections: (VirtualAddress, VirtualSize, RawPointer, RawSize)"""
    for va, vsize, raw_ptr, raw_size in sections:
        span = max(vsize, raw_size)
        if va <= rva < va + span:
            return raw_ptr + (rva - va)
    return None


def _dir_entries(data: bytes, dir_off: int) -> List[Tuple[int, int]]:
    """读一个 IMAGE_RESOURCE_DIRECTORY 的条目表 -> [(Name, OffsetToData), ...]"""
    if dir_off + 16 > len(data):
        return []
    named, by_id = struct.unpack_from("<HH", data, dir_off + 12)
    out: List[Tuple[int, int]] = []
    base = dir_off + 16
    for i in range(named + by_id):
        off = base + i * 8
        if off + 8 > len(data):
            break
        out.append(struct.unpack_from("<II", data, off))
    return out


def _collect_leaves(data: bytes, res_base: int, dir_off: int) -> Dict[int, Tuple[int, int]]:
    """遍历到第三层（语言），返回 {资源ID: (数据RVA, 大小)}"""
    out: Dict[int, Tuple[int, int]] = {}
    for name, offset in _dir_entries(data, dir_off):
        if not (offset & 0x80000000):
            continue
        rid = name & 0x7FFFFFFF
        sub = res_base + (offset & 0x7FFFFFFF)
        for _lname, loff in _dir_entries(data, sub):
            if loff & 0x80000000:
                continue
            entry_off = res_base + loff
            if entry_off + 8 > len(data):
                continue
            drva, dsize = struct.unpack_from("<II", data, entry_off)
            out[rid] = (drva, dsize)
            break
    return out


def extract_icon_ico(exe_path: str) -> Optional[bytes]:
    """提取 exe 内嵌图标，返回完整 ICO 字节流。失败返回 None。"""
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
    except OSError:
        return None

    if len(data) < 0x40 or data[:2] != b"MZ":
        return None

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return None

    coff = e_lfanew + 4
    try:
        num_sections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    except struct.error:
        return None

    opt = coff + 20
    if opt + 2 > len(data):
        return None
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:        # PE32
        dd_off = opt + 96
    elif magic == 0x20B:      # PE32+
        dd_off = opt + 112
    else:
        return None

    # 数据目录第 2 项 = 资源表
    try:
        res_rva, _res_size = struct.unpack_from("<II", data, dd_off + 2 * 8)
    except struct.error:
        return None
    if not res_rva:
        return None

    # 节表
    sec_off = opt + opt_size
    sections: List[Tuple[int, int, int, int]] = []
    for i in range(num_sections):
        base = sec_off + i * 40
        if base + 40 > len(data):
            break
        vsize = struct.unpack_from("<I", data, base + 8)[0]
        va = struct.unpack_from("<I", data, base + 12)[0]
        raw_size = struct.unpack_from("<I", data, base + 16)[0]
        raw_ptr = struct.unpack_from("<I", data, base + 20)[0]
        sections.append((va, vsize, raw_ptr, raw_size))

    res_off = _rva_to_offset(sections, res_rva)
    if res_off is None:
        return None

    icons_dir = groups_dir = None
    for name, offset in _dir_entries(data, res_off):
        if name & 0x80000000:
            continue                     # 命名资源，图标一定是数字 ID
        if not (offset & 0x80000000):
            continue
        sub = res_off + (offset & 0x7FFFFFFF)
        if name == RT_ICON:
            icons_dir = sub
        elif name == RT_GROUP_ICON:
            groups_dir = sub
    if icons_dir is None or groups_dir is None:
        return None

    icons = _collect_leaves(data, res_off, icons_dir)     # id -> (rva, size)
    groups = _collect_leaves(data, res_off, groups_dir)   # id -> (rva, size)
    if not icons or not groups:
        return None

    # 选尺寸最大的那组图标
    best_entries: List[Tuple[int, int, int, int, int, int, int, int]] = []
    for _gid, (grva, gsize) in groups.items():
        goff = _rva_to_offset(sections, grva)
        if goff is None or goff + 6 > len(data):
            continue
        try:
            _reserved, _type, count = struct.unpack_from("<HHH", data, goff)
        except struct.error:
            continue
        entries = []
        for i in range(count):
            eo = goff + 6 + i * 14
            if eo + 14 > len(data):
                break
            w, h, cc, _r, planes, bits, bir, iid = struct.unpack_from(
                "<BBBBHHIH", data, eo)
            entries.append((w or 256, h or 256, cc, 0, planes, bits, bir, iid))
        if entries and (not best_entries or
                        max(e[0] for e in entries) > max(e[0] for e in best_entries)):
            best_entries = entries

    if not best_entries:
        return None

    # 组装 ICO：ICONDIR + 各 ICONDIRENTRY + 图像数据
    blobs: List[Tuple[Tuple[int, int, int, int, int, int], bytes]] = []
    for (w, h, cc, _r, planes, bits, _bir, iid) in best_entries:
        if iid not in icons:
            continue
        irva, isize = icons[iid]
        ioff = _rva_to_offset(sections, irva)
        if ioff is None:
            continue
        blob = data[ioff:ioff + isize]
        if blob:
            blobs.append(((w, h, cc, planes, bits, len(blob)), blob))

    if not blobs:
        return None

    # 大的排前面，方便调用方直接取第一个
    blobs.sort(key=lambda b: (b[0][0], b[0][5]), reverse=True)

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    dir_entries = b""
    payload = b""
    for (w, h, cc, planes, bits, size), blob in blobs:
        dir_entries += struct.pack(
            "<BBBBHHII",
            0 if w >= 256 else w,
            0 if h >= 256 else h,
            cc, 0, planes, bits, size, offset)
        payload += blob
        offset += size

    return header + dir_entries + payload


def extract_largest_png(exe_path: str) -> Optional[bytes]:
    """若最高分辨率图标本身是 PNG 压缩的，直接返回 PNG 字节流（浏览器最省事）。"""
    ico = extract_icon_ico(exe_path)
    if not ico or len(ico) < 22:
        return None
    try:
        count = struct.unpack_from("<H", ico, 4)[0]
        if count < 1:
            return None
        size, off = struct.unpack_from("<II", ico, 6 + 8)
    except struct.error:
        return None
    blob = ico[off:off + size]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return blob
    return None


def save_icon(exe_path: str, out_dir: str, key: str) -> Tuple[str, str]:
    """提取图标并落盘。

    返回 (扩展名, 文件路径)。优先保存 PNG（若图标内嵌 PNG），否则保存 ICO。
    失败返回 ("", "")。
    """
    if not exe_path or not os.path.isfile(exe_path):
        return "", ""

    png = extract_largest_png(exe_path)
    if png:
        path = os.path.join(out_dir, f"{key}.png")
        if _write_if_newer(path, png, exe_path):
            return "png", path
        return "png", path

    ico = extract_icon_ico(exe_path)
    if ico:
        path = os.path.join(out_dir, f"{key}.ico")
        if _write_if_newer(path, ico, exe_path):
            return "ico", path
        return "ico", path

    return "", ""


def _write_if_newer(path: str, payload: bytes, source: str) -> bool:
    """源文件没变就跳过写入，避免重复 IO。"""
    try:
        if os.path.isfile(path) and os.path.getmtime(path) >= os.path.getmtime(source):
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(payload)
        return True
    except OSError:
        return False
