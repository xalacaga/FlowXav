"""Réalisateur caméra : tient une vue verrouillée tant qu'une commande est montrée.

MSFS laisse cliquer dans le cockpit pendant que FlowXav tient la caméra, mais
bloque la vue à la souris : c'est voulu, l'anneau reste ainsi sur la commande.
La vue doit être renvoyée en continu (CameraSet à ~30 Hz). À la libération,
MSFS revient à la vue pilote.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from flowxav.sim.simconnect import Pose, SimConnectClient, SimConnectError

LOGGER = logging.getLogger(__name__)
RATE_S = 0.033


class CameraDirector:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav camera")):
        self._client_factory = client_factory
        self._pose: Pose | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def show(self, pose: Pose) -> None:
        with self._lock:
            self._pose = pose
        self._ensure_started()
        self._wake.set()

    def release(self) -> None:
        with self._lock:
            self._pose = None
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
        acquired = False
        try:
            while not self._stop.is_set():
                with self._lock:
                    pose = self._pose
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
