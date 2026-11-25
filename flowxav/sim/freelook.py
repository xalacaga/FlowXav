"""Regard libre pendant que FlowXav tient la caméra.

Quand FlowXav tient la caméra, MSFS ne réagit plus à la souris pour regarder
autour : FlowXav le fait à sa place, autour du MÊME œil, sans saut de vue,
pour que l'anneau reste exactement sur la commande (simple rotation).

- clic droit maintenu + glisser : tourner la tête ;
- molette : zoomer / dézoomer ;
- clic milieu : rendre la caméra à MSFS (`on_release`) — MSFS revient alors
  à sa propre vue pilote, et l'anneau suit la vue réelle.

Les événements ne sont jamais avalés : ils continuent vers MSFS. Le crochet
souris n'agit que si le pointeur est dans la zone client de MSFS.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from flowxav.sim.window import msfs_client_rect

LOGGER = logging.getLogger(__name__)
WM_MOUSEMOVE, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MOUSEWHEEL = 0x0200, 0x0204, 0x0205, 0x0207, 0x020A
PITCH_LIMIT = 85.0
FOV_MIN, FOV_MAX = 0.25, 1.6


@dataclass
class Offsets:
    pitch: float = 0.0      # degrés, positif vers le bas
    heading: float = 0.0    # degrés, positif vers la droite
    zoom: float = 1.0       # facteur appliqué au champ de vision


class FreeLook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offsets = Offsets()
        self._fov = 1.0
        self._dragging = False
        self._last: tuple[int, int] | None = None
        self._area: tuple[int, int, int, int] | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self.active = False                          # True seulement pendant que FlowXav tient la vue
        self.on_release: Callable[[], None] | None = None

    # -------------------------------------------------------------- état
    def reset(self, fov: float) -> None:
        with self._lock:
            self._offsets = Offsets()
            self._fov = fov

    def offsets(self) -> Offsets:
        with self._lock:
            return Offsets(self._offsets.pitch, self._offsets.heading, self._offsets.zoom)

    # -------------------------------------------------------------- mouvements (testables sans crochet)
    def drag(self, dx: int, dy: int, height: int) -> None:
        """Déplacement de la souris bouton droit enfoncé : la vue suit la main."""
        with self._lock:
            degrees_per_px = (180 / 3.141592653589793) * self._fov * self._offsets.zoom / max(height, 1)
            self._offsets.heading += dx * degrees_per_px
            self._offsets.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self._offsets.pitch + dy * degrees_per_px))

    def wheel(self, notches: float) -> None:
        """Molette bouton droit enfoncé : vers l'avant on zoome."""
        with self._lock:
            zoom = self._offsets.zoom * (0.9 ** notches)
            self._offsets.zoom = max(FOV_MIN / self._fov, min(FOV_MAX / self._fov, zoom))

    # -------------------------------------------------------------- crochet Windows
    def start(self) -> None:
        if sys.platform != "win32" or (self._thread and self._thread.is_alive()):
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._hook, name="flowxav-freelook", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)

    def close(self) -> None:
        """Retire le crochet global et arrête son fil de messages Windows."""
        thread = self._thread
        if not thread or not thread.is_alive():
            return
        thread_id = self._thread_id
        if thread_id:
            import ctypes as ct
            ct.windll.user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT
        if thread is not threading.current_thread():
            thread.join(timeout=2)

    def _hook(self) -> None:
        import ctypes as ct
        from ctypes import wintypes

        user32, kernel32 = ct.windll.user32, ct.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        hookproc = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = (ct.c_int, hookproc, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.restype = ct.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        class MSLLHOOKSTRUCT(ct.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ct.c_size_t)]

        def inside(x: int, y: int) -> bool:
            area = self._area
            return bool(area) and area[0] <= x < area[0] + area[2] and area[1] <= y < area[1] + area[3]

        @hookproc
        def proc(code, wparam, lparam):
            try:
                if code == 0 and self.active:
                    info = ct.cast(lparam, ct.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y = info.pt.x, info.pt.y
                    if wparam in (WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL):
                        self._area = msfs_client_rect()
                    if wparam == WM_RBUTTONDOWN:
                        self._dragging, self._last = inside(x, y), (x, y)
                    elif wparam == WM_RBUTTONUP:
                        self._dragging, self._last = False, None
                    elif wparam == WM_MOUSEMOVE and self._dragging and self._last:
                        self.drag(x - self._last[0], y - self._last[1], self._area[3] if self._area else 1080)
                        self._last = (x, y)
                    elif wparam == WM_MOUSEWHEEL and inside(x, y):
                        self.wheel(ct.c_short(info.mouseData >> 16).value / 120)
                    elif wparam == WM_MBUTTONDOWN and inside(x, y) and self.on_release:
                        self.on_release()
            except Exception:   # pragma: no cover - un crochet ne doit jamais lever
                LOGGER.exception("Regard libre")
            return user32.CallNextHookEx(None, code, wparam, lparam)

        hook = user32.SetWindowsHookExW(14, proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            LOGGER.warning("Regard libre indisponible (crochet souris refusé)")
            self._ready.set()
            return
        self._ready.set()
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ct.byref(msg))
                user32.DispatchMessageW(ct.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(hook)
            self._thread_id = None
