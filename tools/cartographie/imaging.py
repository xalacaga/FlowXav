"""Capture d'écran GDI et PNG minimal, sans dépendance (pas de Pillow)."""

from __future__ import annotations

import ctypes as ct
import struct
import zlib
from ctypes import wintypes
from pathlib import Path

user32, gdi32 = ct.windll.user32, ct.windll.gdi32
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ct.c_int, ct.c_int)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = (wintypes.HDC, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wintypes.HDC, ct.c_int, ct.c_int,
                         wintypes.DWORD)
gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ct.c_void_p,
                            ct.c_void_p, wintypes.UINT)
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = (wintypes.HWND,)
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)


class BITMAPINFOHEADER(ct.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ct.c_long), ("biHeight", ct.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ct.c_long), ("biYPelsPerMeter", ct.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab_rgb(left: int, top: int, width: int, height: int) -> bytearray:
    """Pixels RVB (3 octets) d'une zone de l'écran."""
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    gdi32.SelectObject(memory, bitmap)
    gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x00CC0020)   # SRCCOPY
    header = BITMAPINFOHEADER(ct.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ct.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ct.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(None, screen)
    bgra = buffer.raw
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return rgb


def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    stride = width * 3
    rows = b"".join(b"\x00" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def read_png(path: Path) -> tuple[int, int, bytearray]:
    """Lit un PNG RVB 8 bits non entrelacé, filtre 0 (ceux qu'écrit write_png)."""
    data = Path(path).read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    pos, idat = 8, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3 + 1
    return width, height, bytearray(b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height)))


FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
}


class Canvas:
    def __init__(self, width: int, height: int, rgb: bytearray) -> None:
        self.width, self.height, self.rgb = width, height, rgb

    def put(self, x: int, y: int, colour: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.rgb[i:i + 3] = bytes(colour)

    def ring(self, cx: int, cy: int, r: int, colour: tuple[int, int, int]) -> None:
        for dy in range(-r - 1, r + 2):
            for dx in range(-r - 1, r + 2):
                if r - 1 <= (dx * dx + dy * dy) ** 0.5 <= r + 1:
                    self.put(cx + dx, cy + dy, colour)

    def number(self, x: int, y: int, text: str, colour: tuple[int, int, int], scale: int = 2) -> None:
        for i, ch in enumerate(text):
            for row, bits in enumerate(FONT[ch]):
                for col, bit in enumerate(bits):
                    for sy in range(scale):
                        for sx in range(scale):
                            self.put(x + i * 4 * scale + col * scale + sx, y + row * scale + sy,
                                     colour if bit == "1" else (0, 0, 0))
