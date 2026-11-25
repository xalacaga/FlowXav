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


def _raw_flight_phase(values: dict[str, Any]) -> str:
    if "SIM ON GROUND" not in values:
        return ""

    def number(name: str) -> float:
        value = values.get(name, 0.0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    on_ground = number("SIM ON GROUND") >= 0.5
    speed = number("GROUND VELOCITY")
    if on_ground:
        if number("FLOWXAV_AIRBORNE_SEEN"):
            if speed > 3 or number("GENERAL ENG COMBUSTION:1") or number("GENERAL ENG COMBUSTION:2"):
                return "apres_atterrissage"
            return "fin_vol"
        if speed > 3:
            return "roulage"
        if number("GENERAL ENG COMBUSTION:1") or number("GENERAL ENG COMBUSTION:2"):
            return "sol_moteurs"
        return "parking"
    agl, vertical_speed = number("PLANE ALT ABOVE GROUND"), number("VERTICAL SPEED")
    if agl < 2500 and vertical_speed < -200:
        return "approche"
    if agl < 1500 and vertical_speed > 200:
        return "montee_initiale"
    if vertical_speed < -300:
        return "descente"
    if vertical_speed > 300:
        return "montee"
    altitude, cruise_altitude = number("INDICATED ALTITUDE"), number("FMC_CruiseAlt")
    if 0 < cruise_altitude < 1000:       # certains SDK publient FL350, d'autres 35000 ft
        cruise_altitude *= 100
    if cruise_altitude and altitude < cruise_altitude - 1500:
        return "descente" if number("FLOWXAV_DESCENT_SEEN") else "montee"
    return "croisiere"


def flight_phase(values: dict[str, Any]) -> str:
    """Phase stabilisée si le simulateur l'a fournie, sinon estimation instantanée testable."""
    return str(values.get("FLOWXAV_PHASE") or _raw_flight_phase(values))


def ground_values(config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Valeurs sol calculées : FLOWXAV_PORTES_OUVERTES = nombre de portes ou soutes ouvertes.

    `config["portes"]` liste les variables d'ouverture (0 fermée … 100 ouverte). Rien
    n'est calculé si l'une manque : une porte non lue ne doit pas passer pour fermée.
    """
    doors = config.get("portes", [])
    if not doors or any(values.get(name) is None for name in doors):
        return {}
    return {"FLOWXAV_PORTES_OUVERTES": sum(1 for name in doors if float(values[name]) > 2)}


class FlightPhaseTracker:
    """Évite qu'une turbulence ou un palier bref change la procédure proposée."""

    def __init__(self, hold_s: float = 6.0) -> None:
        self.hold_s = hold_s
        self.phase = ""
        self._candidate = ""
        self._since = 0.0

    def update(self, values: dict[str, Any], now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        candidate = _raw_flight_phase(values)
        if not self.phase or not candidate:
            self.phase = candidate
            self._candidate, self._since = candidate, now
        elif candidate == self.phase:
            self._candidate, self._since = candidate, now
        elif candidate != self._candidate:
            self._candidate, self._since = candidate, now
        elif now - self._since >= self.hold_s:
            self.phase = candidate
        return self.phase


@dataclass
class Snapshot:
    connected: bool = False
    title: str = ""
    profile_id: str | None = None
    source_ok: bool = False
    source_message: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    updated: float = 0.0
    cdu: list[str] = field(default_factory=list)      # lignes de l'écran du CDU, si l'avion le publie
    phase: str = ""

    def public(self) -> dict[str, Any]:
        return {"connecte": self.connected, "avion": self.title, "profil": self.profile_id,
                "etat_disponible": self.source_ok, "message_etat": self.source_message, "phase_vol": self.phase}


class Simulator:
    def __init__(self, client_factory: Callable[[], SimConnectClient] = lambda: SimConnectClient("FlowXav etat"),
                 forced_profile: str | None = None) -> None:
        self._client_factory = client_factory
        self._forced_profile = forced_profile
        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="flowxav-simulateur", daemon=True)
        self._airborne_seen = False
        self._descent_seen = False
        self._phase_tracker = FlightPhaseTracker()

    def start(self) -> None:
        self._thread.start()

    def reset_flight(self) -> None:
        """Oublie le vol précédent (décollage, descente vus) : nouvel avion ou état de départ rechargé.

        Sans cela, après un premier vol, un avion remis en Cold & Dark serait vu en « fin de vol »
        et FlowXav proposerait la mauvaise procédure.
        """
        self._airborne_seen = False
        self._descent_seen = False
        self._phase_tracker = FlightPhaseTracker()
        LOGGER.info("Suivi de phase remis à zéro")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(**{**self._snapshot.__dict__, "values": dict(self._snapshot.values),
                               "cdu": list(self._snapshot.cdu)})

    def profile(self) -> Profile | None:
        profile_id = self.snapshot().profile_id
        return load_profile(profile_id) if profile_id else None

    def _update(self, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._snapshot, name, value)

    @staticmethod
    def _read_cdu(client: SimConnectClient, profile: Profile) -> list[str]:
        """Écran du CDU (pour placer l'anneau sur la bonne touche) ; vide si l'avion ne le publie pas."""
        config = profile.data.get("cdu")
        if not config:
            return []
        try:
            from flowxav.state.pmdg_cdu import SCREEN_SIZE, decode_screen
            return decode_screen(client.read_client_data(config["zone"], SCREEN_SIZE, timeout_s=0.5))[1]
        except SimConnectError as error:
            if "fermé" in str(error):
                raise
            return []

    @staticmethod
    def _read_flight_tracking(client: SimConnectClient, profile: Profile) -> dict[str, float]:
        variables = [tuple(item) for item in profile.data.get("suivi_vol", {}).get("simvars", [])]
        if not variables:
            return {}
        try:
            return client.read_simvars(variables, timeout_s=1.0)
        except SimConnectError as error:
            if "fermé" in str(error):
                raise
            LOGGER.debug("Suivi de phase indisponible : %s", error)
            return {}

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
                    if (found and found.id) != (profile and profile.id) or title != self._snapshot.title:
                        if (found and found.id) != (profile and profile.id):
                            LOGGER.info("Avion reconnu : %s", found.id if found else "aucun profil")
                        self.reset_flight()
                    profile = found
                    self._update(title=title, profile_id=profile.id if profile else None)
                if profile is None:
                    self._update(source_ok=False, source_message="", values={})
                    self._stop.wait(1.0)
                    continue
                try:
                    values = profile.source.read(client)
                    values.update(self._read_flight_tracking(client, profile))
                    values.update(ground_values(profile.data.get("sol", {}), values))
                    if values.get("SIM ON GROUND") is not None and float(values["SIM ON GROUND"]) < 0.5:
                        self._airborne_seen = True
                    if (self._airborne_seen and float(values.get("VERTICAL SPEED", 0)) < -300
                            and float(values.get("PLANE ALT ABOVE GROUND", 0)) > 2500):
                        self._descent_seen = True
                    values["FLOWXAV_AIRBORNE_SEEN"] = self._airborne_seen
                    values["FLOWXAV_DESCENT_SEEN"] = self._descent_seen
                    phase = self._phase_tracker.update(values)
                    values["FLOWXAV_PHASE"] = phase
                    self._update(source_ok=True, source_message="", values=values, updated=time.monotonic(),
                                 cdu=self._read_cdu(client, profile), phase=phase)
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
                self._update(connected=False, title="", profile_id=None, source_ok=False, values={}, phase="")
                self._stop.wait(RETRY_S)
            except Exception:   # pragma: no cover - le service ne doit jamais mourir
                LOGGER.exception("Service simulateur")
                self._stop.wait(RETRY_S)
        if client is not None:
            client.close()
