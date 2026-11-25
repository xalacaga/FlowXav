"""Détecte que l'utilisateur veut regarder ailleurs pendant que FlowXav cadre une commande.

FlowXav place d'abord la vue face au panneau ; dès que l'utilisateur touche à
la vue au-dessus de MSFS (clic droit ou clic milieu enfoncé, molette), il
rend la caméra pour que MSFS reprenne la main et que le regard soit libre.
Les clics gauches (manœuvrer une commande) ne comptent pas.

Les événements ne sont jamais avalés : ils continuent vers MSFS. Le crochet
n'agit que si le pointeur est dans la zone client de MSFS.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

from flowxav.sim.window import msfs_client_rect

LOGGER = logging.getLogger(__name__)
WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0204, 0x0207, 0x020A, 0x020E
LOOK_MESSAGES = {WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL, WM_MOUSEHWHEEL}


def is_look_input(message: int) -> bool:
    return message in LOOK_MESSAGES


class LookInput:
    def __init__(self) -> None:
        self.on_look: Callable[[], None] | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if sys.platform != "win32" or (self._thread and self._thread.is_alive()):
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._hook, name="flowxav-lookinput", daemon=True)
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

        @hookproc
        def proc(code, wparam, lparam):
            try:
                callback = self.on_look
                if code == 0 and callback and is_look_input(wparam):
                    info = ct.cast(lparam, ct.POINTER(MSLLHOOKSTRUCT)).contents
                    area = msfs_client_rect()
                    x, y = info.pt.x, info.pt.y
                    if area and area[0] <= x < area[0] + area[2] and area[1] <= y < area[1] + area[3]:
                        callback()
            except Exception:   # pragma: no cover - un crochet ne doit jamais lever
                LOGGER.exception("Détection du regard")
            return user32.CallNextHookEx(None, code, wparam, lparam)

        hook = user32.SetWindowsHookExW(14, proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            LOGGER.warning("Détection du regard indisponible (crochet souris refusé)")
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
