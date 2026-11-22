"""Service simulateur : connexion, reconnaissance de l'avion, lecture continue de son état.

Un seul fil possède la connexion d'état ; les autres lisent un instantané.
La connexion se rétablit seule si MSFS est lancé après FlowXav ou redémarre.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from flowxav.aircraft import Profile, detect, load_profile
from flowxav.sim.simconnect import SimConnectClient, SimConnectError

LOGGER = logging.getLogger(__name__)
STATE_PERIOD_S = 0.25
TITLE_PERIOD_S = 5.0
RETRY_S = 5.0


@dataclass
class Snapshot:
    connected: bool = False
    title: str = ""
    profile_id: str | None = None
    source_ok: bool = False
    source_message: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    updated: float = 0.0

    def public(self) -> dict[str, Any]:
        return {"connecte": self.connected, "avion": self.title, "profil": self.profile_id,
                "etat_disponible": self.source_ok, "message_etat": self.source_message}


class Simulator:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav etat"),
                 forced_profile: str | None = None) -> None:
        self._client_factory = client_factory
        self._forced_profile = forced_profile
        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="flowxav-simulateur", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(**{**self._snapshot.__dict__, "values": dict(self._snapshot.values)})

    def profile(self) -> Profile | None:
        profile_id = self.snapshot().profile_id
        return load_profile(profile_id) if profile_id else None

    def _update(self, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._snapshot, name, value)

    def _run(self) -> None:
        client: SimConnectClient | None = None
        profile: Profile | None = None
        next_title = 0.0
        while not self._stop.is_set():
            try:
                if client is None:
                    client = self._client_factory()
                    self._update(connected=True)
                    LOGGER.info("Connecté à MSFS")
                now = time.monotonic()
                if now >= next_title:
                    next_title = now + TITLE_PERIOD_S
                    title = client.read_title()
                    found = load_profile(self._forced_profile) if self._forced_profile else detect(title)
                    if (found and found.id) != (profile and profile.id):
                        LOGGER.info("Avion reconnu : %s", found.id if found else "aucun profil")
                    profile = found
                    self._update(title=title, profile_id=profile.id if profile else None)
                if profile is None:
                    self._update(source_ok=False, source_message="", values={})
                    self._stop.wait(1.0)
                    continue
                try:
                    values = profile.source.read(client)
                    self._update(source_ok=True, source_message="", values=values, updated=time.monotonic())
                except SimConnectError as error:
                    if "fermé" in str(error):
                        raise
                    self._update(source_ok=False, source_message=profile.source.help() or str(error), values={})
                    self._stop.wait(2.0)
                    continue
                self._stop.wait(STATE_PERIOD_S)
            except SimConnectError as error:
                if client is not None:
                    LOGGER.info("Connexion MSFS perdue : %s", error)
                    client.close()
                client, profile = None, None
                self._update(connected=False, title="", profile_id=None, source_ok=False, values={})
                self._stop.wait(RETRY_S)
            except Exception:   # pragma: no cover - le service ne doit jamais mourir
                LOGGER.exception("Service simulateur")
                self._stop.wait(RETRY_S)
        if client is not None:
            client.close()
