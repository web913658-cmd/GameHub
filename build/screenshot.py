"""截取当前桌面（含 GameHub 应用窗口），保存为 PNG。

纯 ctypes + zlib 实现，不依赖任何第三方库。
"""

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import zlib

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def capture() -> tuple:
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, x, y, SRCCOPY)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # 负数 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)

    return w, h, buf.raw


def to_png(w: int, h: int, bgra: bytes) -> bytes:
    row_bytes = w * 4
    raw = bytearray()
    for row in range(h):
        raw.append(0)                                  # filter: none
        line = bytearray(bgra[row * row_bytes:(row + 1) * row_bytes])
        line[0::4], line[2::4] = line[2::4], line[0::4]  # BGRA -> RGBA
        raw += line

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)   # 8bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "desktop.png"
    w, h, data = capture()
    png = to_png(w, h, data)
    with open(out, "wb") as f:
        f.write(png)
    print(f"已截屏 {w}x{h} -> {out}  ({len(png)} bytes)")
