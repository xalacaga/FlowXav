"""Suivi de la caméra : FlowXav ne prend JAMAIS la caméra de MSFS.

Le regard reste entièrement libre (souris, clavier, TrackIR, vues
personnalisées, zoom) même pendant qu'une commande est montrée. FlowXav lit
seulement la vue réellement affichée (CameraGet, ~30 Hz) pour que l'anneau
reste sur la commande ; hors champ, une flèche au bord de l'écran indique de
quel côté regarder (voir overlay).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from flowxav.sim.simconnect import Pose, SimConnectClient, SimConnectError

LOGGER = logging.getLogger(__name__)
RATE_S = 0.033
READ_TIMEOUT_S = 0.5


class CameraDirector:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav camera")):
        self._client_factory = client_factory
        self._reference: Pose | None = None
        self._observed: Pose | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def show(self, pose: Pose) -> None:
        """Commence à suivre la vue du joueur pour une commande relevée dans `pose`.

        La caméra n'est pas déplacée : `pose` sert seulement de repère à l'anneau.
        """
        with self._lock:
            self._reference = pose
            self._observed = None
        self._ensure_started()
        self._wake.set()

    def current_pose(self) -> Pose | None:
        """Vue réellement affichée par MSFS (None tant qu'elle n'a pas été lue)."""
        with self._lock:
            return self._observed if self._reference is not None else None

    def release(self) -> None:
        with self._lock:
            self._reference = None
            self._observed = None
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="flowxav-camera", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        client: SimConnectClient | None = None
        try:
            while not self._stop.is_set():
                with self._lock:
                    following = self._reference is not None
                if not following:
                    self._wake.wait(timeout=0.5)
                    self._wake.clear()
                    continue
                try:
                    if client is None:
                        client = self._client_factory()
                    pose = client.camera_get(timeout_s=READ_TIMEOUT_S)
                    with self._lock:
                        if self._reference is not None:
                            self._observed = pose
                    self.error = None
                except SimConnectError as error:
                    self.error = str(error)
                    LOGGER.warning("Caméra : %s", error)
                    if client:
                        client.close()
                    client = None
                    self._stop.wait(2)
                    continue
                time.sleep(RATE_S)
        finally:
            if client:
                client.close()
