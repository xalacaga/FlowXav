"""Surimpression : un anneau qui clignote sur la commande, par-dessus MSFS.

Une fenêtre Tk transparente, toujours au premier plan, qui laisse passer les
clics (WS_EX_LAYERED | WS_EX_TRANSPARENT) et ne prend jamais le focus. Elle
couvre la zone client de MSFS et tourne dans son propre fil : les appels
publics sont sûrs depuis n'importe quel fil.

Limites connues : plein écran fenêtré ou fenêtré uniquement (pas de plein
écran exclusif), pas de VR.
"""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass

from flowxav.sim.window import msfs_client_rect

LOGGER = logging.getLogger(__name__)

TRANSPARENT = "#ff00ff"
WAITING, DONE = "#ffb000", "#30d158"
WS_EX = 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8   # LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE, TOPMOST


@dataclass
class Target:
    u: float
    v: float
    label: str
    done: bool = False


class Overlay:
    def __init__(self) -> None:
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # -------------------------------------------------------------- API publique (tout fil)
    def show(self, u: float, v: float, label: str) -> None:
        self._ensure_started()
        self._commands.put(("show", Target(u, v, label)))

    def mark_done(self) -> None:
        self._commands.put(("done", None))

    def hide(self) -> None:
        self._commands.put(("hide", None))

    def close(self) -> None:
        if self._thread and self._thread.is_alive():
            self._commands.put(("quit", None))
            self._thread.join(timeout=2)

    # -------------------------------------------------------------- fil Tk
    def _ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="flowxav-overlay", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        root.attributes("-transparentcolor", TRANSPARENT)
        canvas = tk.Canvas(root, bg=TRANSPARENT, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        ring = canvas.create_oval(0, 0, 0, 0, outline=WAITING, width=5)
        halo = canvas.create_oval(0, 0, 0, 0, outline=WAITING, width=2)
        label = canvas.create_text(0, 0, text="", fill=WAITING, font=("Segoe UI", 16, "bold"))
        state = {"target": None, "area": None, "placed": 0.0}
        root.withdraw()
        root.update_idletasks()
        if sys.platform == "win32":
            import ctypes as ct
            user32 = ct.windll.user32
            hwnd = user32.GetParent(root.winfo_id())
            user32.SetWindowLongW(hwnd, -20, user32.GetWindowLongW(hwnd, -20) | WS_EX)
        self._ready.set()

        def place(now: float) -> None:
            if now - state["placed"] < 1.0:
                return
            state["placed"] = now
            area = msfs_client_rect()
            if area and area != state["area"]:
                state["area"] = area
                root.geometry(f"{area[2]}x{area[3]}+{area[0]}+{area[1]}")
                canvas.configure(width=area[2], height=area[3])
            root.attributes("-topmost", True)

        def tick() -> None:
            now = time.monotonic()
            try:
                while True:
                    command, payload = self._commands.get_nowait()
                    if command == "quit":
                        root.destroy()
                        return
                    if command == "show":
                        state["target"], state["placed"] = payload, 0.0
                        root.deiconify()
                    elif command == "done" and state["target"]:
                        state["target"].done = True
                    elif command == "hide":
                        state["target"] = None
                        root.withdraw()
            except queue.Empty:
                pass
            target = state["target"]
            if target:
                place(now)
                area = state["area"]
                if area:
                    phase = (math.sin(now * 2 * math.pi * 1.2) + 1) / 2
                    colour = DONE if target.done else WAITING
                    cx, cy = target.u * area[2], target.v * area[3]
                    r, h = 28 + 9 * phase, 13 * phase
                    canvas.coords(ring, cx - r, cy - r, cx + r, cy + r)
                    canvas.coords(halo, cx - r - h, cy - r - h, cx + r + h, cy + r + h)
                    canvas.itemconfigure(ring, outline=colour, width=3 + 3 * phase)
                    canvas.itemconfigure(halo, outline=colour)
                    below = cy + 64 if cy < area[3] - 90 else cy - 64
                    canvas.coords(label, cx, below)
                    canvas.itemconfigure(label, fill=colour, text=target.label + (" ✓" if target.done else ""))
            root.after(33, tick)

        root.after(10, tick)
        try:
            root.mainloop()
        except Exception:   # pragma: no cover - fil d'affichage, ne doit jamais tuer l'application
            LOGGER.exception("Surimpression arrêtée")
