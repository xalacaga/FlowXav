"""Caméra : FlowXav place la vue face au panneau, l'utilisateur regarde autour sans saut.

1. Cadrage : à chaque commande montrée, FlowXav prend la caméra et place la
   vue de référence, l'anneau sur la commande (CameraSet renvoyé en continu,
   ~30 Hz, sinon MSFS reprend la vue). Clic droit glissé et molette font
   tourner et zoomer la vue autour du même œil (voir freelook) : pas de saut,
   l'anneau reste exactement sur la commande.
2. Clic milieu : la caméra est rendue à MSFS (qui revient à sa vue pilote) ;
   FlowXav ne fait plus que LIRE la vue réelle (CameraGet) pour que l'anneau
   suive la commande ; hors champ, une flèche indique où regarder.
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
READ_TIMEOUT_S = 0.5


class CameraDirector:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav camera"),
                 freelook: FreeLook | None = None):
        self._client_factory = client_factory
        self.freelook = freelook or FreeLook()
        self.freelook.on_release = self.free
        self._reference: Pose | None = None
        self._framing = False                    # True : FlowXav tient la vue de référence
        self._observed: Pose | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def show(self, pose: Pose) -> None:
        """Place la vue de référence de la commande ; elle reste tenue jusqu'à ce que l'utilisateur bouge."""
        with self._lock:
            self._reference, self._framing, self._observed = pose, True, None
        self.freelook.reset(pose.fov)
        self.freelook.active = True
        self.freelook.start()
        self._ensure_started()
        self._wake.set()

    def free(self) -> None:
        """L'utilisateur regarde ailleurs : la caméra est rendue à MSFS, l'anneau suit la vue réelle."""
        with self._lock:
            if not self._framing:
                return
            self._framing, self._observed = False, None
        self.freelook.active = False
        LOGGER.info("Caméra rendue à l'utilisateur")
        self._wake.set()

    def current_pose(self) -> Pose | None:
        """Vue affichée : la référence pendant le cadrage, sinon la vue lue dans MSFS (None tant qu'inconnue)."""
        with self._lock:
            if self._reference is None:
                return None
            if not self._framing:
                return self._observed
            reference = self._reference
        offsets = self.freelook.offsets()
        return replace(reference, pitch=reference.pitch + offsets.pitch, heading=reference.heading + offsets.heading,
                       fov=reference.fov * offsets.zoom)

    def release(self) -> None:
        with self._lock:
            self._reference, self._framing, self._observed = None, False, None
        self.freelook.active = False
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.freelook.close()

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
                    reference, framing = self._reference, self._framing
                if reference is None or (acquired and not framing):
                    if acquired and client:
                        self._safe(client.camera_release)
                        acquired = False
                    if reference is None:
                        self._wake.wait(timeout=0.5)
                        self._wake.clear()
                        continue
                try:
                    if client is None:
                        client = self._client_factory()
                    if framing:
                        if not acquired:
                            client.camera_acquire()
                            acquired = True
                        client.camera_set(self.current_pose() or reference)
                        for _ in client.messages():
                            pass
                    else:
                        pose = client.camera_get(timeout_s=READ_TIMEOUT_S)
                        with self._lock:
                            if self._reference is not None and not self._framing:
                                self._observed = pose
                    self.error = None
                except SimConnectError as error:
                    self.error = str(error)
                    LOGGER.warning("Caméra : %s", error)
                    if client:
                        client.close()
                    client, acquired = None, False
                    self._stop.wait(2)
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
