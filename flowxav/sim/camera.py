"""Réalisateur caméra : tient une vue verrouillée tant qu'une commande est montrée.

MSFS laisse cliquer dans le cockpit pendant que FlowXav tient la caméra, mais
bloque la vue à la souris. FlowXav prend alors le regard à son compte (voir
freelook) : clic droit glissé pour tourner, clic droit + molette pour zoomer,
autour du même œil, pour que l'anneau reste sur la commande. La vue doit être
renvoyée en continu (CameraSet à ~30 Hz). À la libération, MSFS revient à la
vue pilote.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Callable

from flowxav.sim.freelook import FreeLook
from flowxav.sim.simconnect import Pose, SimConnectClient, SimConnectError

LOGGER = logging.getLogger(__name__)
RATE_S = 0.033


class CameraDirector:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav camera"),
                 freelook: FreeLook | None = None):
        self._client_factory = client_factory
        self.freelook = freelook or FreeLook()
        self._pose: Pose | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def show(self, pose: Pose) -> None:
        """Cadre la vue de référence (le regard libre repart de zéro)."""
        with self._lock:
            self._pose = pose
        self.freelook.reset(pose.fov)
        self.freelook.start()
        self._ensure_started()
        self._wake.set()

    def zoom(self, notches: float) -> None:
        """Zoom depuis l'interface : positif pour rapprocher, négatif pour éloigner."""
        if self._pose is not None:
            self.freelook.wheel(notches)

    def recenter(self) -> None:
        """Revient à la vue de référence de la commande (sans rotation ni zoom)."""
        with self._lock:
            pose = self._pose
        if pose is not None:
            self.freelook.reset(pose.fov)

    def current_pose(self) -> Pose | None:
        """Vue réellement affichée : référence + regard libre de l'utilisateur."""
        with self._lock:
            pose = self._pose
        if pose is None:
            return None
        offsets = self.freelook.offsets()
        return replace(pose, pitch=pose.pitch + offsets.pitch, heading=pose.heading + offsets.heading,
                       fov=pose.fov * offsets.zoom)

    def release(self) -> None:
        with self._lock:
            self._pose = None
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
        close_freelook = getattr(self.freelook, "close", None)
        if callable(close_freelook):
            close_freelook()

    def _ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="flowxav-camera", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        client: SimConnectClient | None = None
        acquired = False
        try:
            while not self._stop.is_set():
                pose = self.current_pose()
                if pose is None:
                    if acquired and client:
                        self._safe(client.camera_release)
                        acquired = False
                    self._wake.wait(timeout=0.5)
                    self._wake.clear()
                    continue
                try:
                    if client is None:
                        client = self._client_factory()
                    if not acquired:
                        client.camera_acquire()
                        acquired = True
                    client.camera_set(pose)
                    for _ in client.messages():
                        pass
                    self.error = None
                except SimConnectError as error:
                    self.error = str(error)
                    LOGGER.warning("Caméra : %s", error)
                    if client:
                        client.close()
                    client, acquired = None, False
                    time.sleep(2)
                    continue
                time.sleep(RATE_S)
        finally:
            if client:
                if acquired:
                    self._safe(client.camera_release)
                client.close()

    @staticmethod
    def _safe(call: Callable[[], None]) -> None:
        try:
            call()
        except SimConnectError as error:
            LOGGER.info("Caméra : %s", error)
