"""Fenêtre de Microsoft Flight Simulator : position de la zone client à l'écran."""

from __future__ import annotations

import ctypes as ct
import sys
from ctypes import wintypes

if sys.platform == "win32":
    user32 = ct.windll.user32
    try:   # coordonnées physiques : indispensable pour poser l'anneau au pixel près
        ct.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
else:   # pragma: no cover - FlowXav ne vise que Windows
    user32 = None

MSFS_TITLE_PREFIX = "Microsoft Flight Simulator"


def msfs_client_rect() -> tuple[int, int, int, int] | None:
    """(gauche, haut, largeur, hauteur) de la zone client de MSFS, ou None s'il n'est pas ouvert."""
    if user32 is None:
        return None
    found: list[int] = []

    @ct.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        buffer = ct.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buffer, 256)
        if buffer.value.startswith(MSFS_TITLE_PREFIX) and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    user32.GetClientRect(found[0], ct.byref(rect))
    user32.ClientToScreen(found[0], ct.byref(origin))
    if rect.right <= 0 or rect.bottom <= 0:
        return None
    return origin.x, origin.y, rect.right, rect.bottom


def screen_point(u: float, v: float, area: tuple[int, int, int, int]) -> tuple[int, int]:
    """Point écran d'une position exprimée en proportion de la zone client."""
    left, top, width, height = area
    return round(left + u * width), round(top + v * height)
