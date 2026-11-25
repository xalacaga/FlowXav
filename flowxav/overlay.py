"""Surimpression : un anneau qui clignote sur la commande, par-dessus MSFS.

Une fenêtre Tk transparente, toujours au premier plan, qui laisse passer les
clics (WS_EX_LAYERED | WS_EX_TRANSPARENT) et ne prend jamais le focus. Elle
couvre la zone client de MSFS et tourne dans son propre fil : les appels
publics sont sûrs depuis n'importe quel fil.

FlowXav cadre d'abord la commande puis rend la caméra dès que l'utilisateur
regarde ailleurs : l'anneau est recalculé à chaque image depuis la vue réelle. Hors
champ, une flèche au bord de l'écran indique de quel côté regarder.

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
from typing import Callable

from flowxav.sim.projection import DEFAULT_DEPTH_M, anchor, project, toward
from flowxav.sim.simconnect import Pose
from flowxav.sim.window import msfs_client_rect

LOGGER = logging.getLogger(__name__)

TRANSPARENT = "#ff00ff"
WAITING, DONE = "#ffb000", "#34d17a"
PILL, PILL_TEXT = "#12171d", "#eef2f6"
FADE_TO = "#1a1408"                     # teinte vers laquelle l'onde s'estompe (pas d'alpha dans Tk)
FONT = ("Segoe UI Semibold", 11)
RING_R, PING_R, PING_PERIOD_S = 15, 36, 1.5
LABEL_S = 4.0          # l'étiquette s'efface ensuite : elle ne doit pas masquer les commandes voisines


def blend(start: str, end: str, amount: float) -> str:
    """Couleur entre `start` et `end` (0 → start, 1 → end), au format #rrggbb."""
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    amount = max(0.0, min(1.0, amount))
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def pill_points(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[float]:
    """Contour d'un rectangle aux coins arrondis, pour create_polygon(smooth=True)."""
    r = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
            x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
WS_EX = 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8   # LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE, TOPMOST


@dataclass
class Target:
    u: float
    v: float
    label: str
    done: bool = False
    reference: Pose | None = None                       # vue où (u, v) a été relevé
    current: Callable[[], Pose | None] | None = None    # vue affichée maintenant
    depth: float = DEFAULT_DEPTH_M                # œil de référence → commande, en mètres
    point: tuple[float, float, float] | None = None
    shown_at: float = 0.0
    done_at: float = 0.0

    def screen(self, aspect: float) -> tuple[float, float, bool] | None:
        """Position actuelle de la commande à l'écran (u, v, visible), None tant que la vue est inconnue."""
        if self.reference is None or self.current is None:
            return self.u, self.v, True
        pose = self.current()
        if pose is None:
            return None
        if self.point is None:
            self.point = anchor(self.reference, self.u, self.v, aspect, self.depth)
        return project(pose, toward(pose, self.point), aspect)


class Overlay:
    def __init__(self) -> None:
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # -------------------------------------------------------------- API publique (tout fil)
    def show(self, u: float, v: float, label: str, reference: Pose | None = None,
             current: Callable[[], Pose | None] | None = None, depth: float = DEFAULT_DEPTH_M) -> None:
        self._ensure_started()
        self._commands.put(("show", Target(u, v, label, reference=reference, current=current, depth=depth)))

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
        ping = canvas.create_oval(0, 0, 0, 0, outline=WAITING, width=2)
        ring = canvas.create_oval(0, 0, 0, 0, outline=WAITING, width=2)
        dot = canvas.create_oval(0, 0, 0, 0, fill=WAITING, outline="")
        arrow = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill=WAITING, outline="")
        pill = canvas.create_polygon(0, 0, 0, 0, 0, 0, smooth=True, fill=PILL, outline=WAITING, width=1)
        mark = canvas.create_oval(0, 0, 0, 0, fill=WAITING, outline="")
        label = canvas.create_text(0, 0, text="", fill=PILL_TEXT, font=FONT, anchor="w")
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
                        payload.shown_at = time.monotonic()
                        state["target"], state["placed"] = payload, 0.0
                        root.deiconify()
                    elif command == "done" and state["target"]:
                        state["target"].done = True
                        state["target"].done_at = time.monotonic()
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
                    colour = DONE if target.done else WAITING
                    text = target.label + ("  ✓" if target.done else "")
                    position = target.screen(area[2] / area[3])
                    if position is None:
                        # Vue de MSFS pas encore lue : rien plutôt qu'un anneau au mauvais endroit.
                        for item in (ring, ping, dot):
                            canvas.coords(item, 0, 0, 0, 0)
                        canvas.coords(arrow, 0, 0, 0, 0, 0, 0)
                        root.after(33, tick)
                        return
                    u, v, visible = position
                    cx, cy = u * area[2], v * area[3]
                    hide = (0, 0, 0, 0)

                    def caption(x: float, y: float) -> None:
                        """Étiquette en pastille sombre À CÔTÉ de l'anneau (pas dessous : elle masquerait
                        la commande voisine), visible quelques secondes puis effacée."""
                        recent = now - target.shown_at < LABEL_S or (target.done and now - target.done_at < 2.0)
                        if not recent:
                            canvas.coords(pill, 0, 0, 0, 0, 0, 0)
                            canvas.coords(mark, 0, 0, 0, 0)
                            canvas.itemconfigure(label, text="")
                            return
                        canvas.itemconfigure(label, text=text)
                        x0, y0, x1, y1 = canvas.bbox(label) or (0, 0, 0, 0)
                        width, height = x1 - x0, y1 - y0
                        box = width + 28
                        gap = RING_R + 14
                        left = x + gap if x + gap + box < area[2] - 6 else x - gap - box
                        left = min(max(left, 6), area[2] - box - 6)
                        canvas.coords(label, left + 18, y)
                        canvas.coords(pill, *pill_points(left, y - height / 2 - 5, left + box, y + height / 2 + 5, 11))
                        canvas.itemconfigure(pill, outline=blend(colour, PILL, 0.35))
                        canvas.coords(mark, left + 8, y - 3, left + 14, y + 3)
                        canvas.itemconfigure(mark, fill=colour)

                    if visible:
                        canvas.coords(arrow, *hide, 0, 0)
                        canvas.coords(ring, cx - RING_R, cy - RING_R, cx + RING_R, cy + RING_R)
                        canvas.itemconfigure(ring, outline=colour)
                        canvas.coords(dot, cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5)
                        canvas.itemconfigure(dot, fill=colour)
                        if target.done:
                            canvas.coords(ping, *hide)
                        else:
                            # Onde qui s'élargit et s'estompe, comme un sonar.
                            wave = (now % PING_PERIOD_S) / PING_PERIOD_S
                            radius = RING_R + (PING_R - RING_R) * wave
                            canvas.coords(ping, cx - radius, cy - radius, cx + radius, cy + radius)
                            canvas.itemconfigure(ping, outline=blend(colour, FADE_TO, wave), width=max(1.0, 2.0 - wave))
                        caption(cx, cy)
                    else:
                        # Hors champ : chevron fin au bord de l'écran, pointé vers la commande.
                        margin = 34
                        cx = min(max(cx, margin), area[2] - margin)
                        cy = min(max(cy, margin), area[3] - margin)
                        ang = math.atan2(cy - area[3] / 2, cx - area[2] / 2)
                        size = 14 + 3 * (math.sin(now * 2 * math.pi) + 1) / 2
                        tip = (cx + size * math.cos(ang), cy + size * math.sin(ang))
                        back_l = (cx + size * 0.9 * math.cos(ang + 2.5), cy + size * 0.9 * math.sin(ang + 2.5))
                        back_r = (cx + size * 0.9 * math.cos(ang - 2.5), cy + size * 0.9 * math.sin(ang - 2.5))
                        canvas.coords(arrow, *tip, *back_l, *back_r)
                        canvas.itemconfigure(arrow, fill=colour)
                        for item in (ring, ping, dot):
                            canvas.coords(item, *hide)
                        caption(cx, cy)
            root.after(33, tick)

        root.after(10, tick)
        try:
            root.mainloop()
        except Exception:   # pragma: no cover - fil d'affichage, ne doit jamais tuer l'application
            LOGGER.exception("Surimpression arrêtée")
