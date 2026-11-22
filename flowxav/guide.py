"""Moteur de guidage : enchaîne les étapes d'une procédure sur le cockpit réel.

Pour chaque étape : la caméra prend la vue de la commande, l'anneau clignote
dessus, et FlowXav attend que l'état de l'avion corresponde à ce qui est
attendu. L'anneau passe au vert, puis l'étape suivante commence.

Une étape sans état lisible (bouton-poussoir, commande EVT_…) se valide par
le bouton « Fait » de l'interface.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from flowxav.aircraft import Profile
from flowxav.overlay import Overlay
from flowxav.sim.camera import CameraDirector
from flowxav.simulator import Simulator

LOGGER = logging.getLogger(__name__)
HOLD_S = 0.8          # l'état attendu doit tenir ce temps (les barres électriques suivent avec retard)
PAUSE_DONE_S = 1.5    # l'anneau reste vert avant l'étape suivante
TICK_S = 0.2


def satisfied(expected: dict[str, Any], value: Any) -> bool:
    """Évalue une condition d'étape : egal, parmi, different, min, max."""
    if value is None:
        return False
    if "egal" in expected and value != expected["egal"]:
        return False
    if "parmi" in expected and value not in expected["parmi"]:
        return False
    if "different" in expected and value == expected["different"]:
        return False
    if "min" in expected and value < expected["min"]:
        return False
    if "max" in expected and value > expected["max"]:
        return False
    return True


def text(entry: Any, language: str) -> str:
    if isinstance(entry, dict):
        return entry.get(language) or entry.get("en") or entry.get("fr") or ""
    return entry or ""


@dataclass
class Session:
    profile: Profile
    procedure: dict[str, Any]
    language: str
    index: int = 0
    held_since: float | None = None
    done_at: float | None = None
    shown_index: int = -1
    finished: bool = False


class Guide:
    def __init__(self, simulator: Simulator, camera: CameraDirector | None = None, overlay: Overlay | None = None):
        self.simulator = simulator
        self.camera = camera or CameraDirector()
        self.overlay = overlay or Overlay()
        self._session: Session | None = None
        self._preview_until = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="flowxav-guide", daemon=True)

    def start_thread(self) -> None:
        self._thread.start()

    # -------------------------------------------------------------- commandes de l'interface
    def start(self, profile: Profile, procedure_id: str, language: str = "fr") -> None:
        with self._lock:
            self._session = Session(profile, profile.procedure(procedure_id), language)
            self._preview_until = 0.0
        LOGGER.info("Procédure %s démarrée sur %s", procedure_id, profile.id)

    def confirm(self) -> None:
        """« Fait » : valide l'étape en cours (utile pour les commandes sans état)."""
        with self._lock:
            if self._session and not self._session.finished and self._session.done_at is None:
                self._session.done_at = time.monotonic()
                self.overlay.mark_done()

    def go(self, delta: int) -> None:
        with self._lock:
            session = self._session
            if session:
                session.index = max(0, min(len(session.procedure["etapes"]) - 1, session.index + delta))
                session.held_since = session.done_at = None
                session.shown_index, session.finished = -1, False

    def stop(self) -> None:
        with self._lock:
            self._session = None
            self._preview_until = 0.0
        self.overlay.hide()
        self.camera.release()

    def preview(self, profile: Profile, key: str, seconds: float = 12.0) -> None:
        """« Montre-moi » : une seule commande, quelques secondes, hors procédure."""
        control = profile.control(key)
        with self._lock:
            self._session = None
            self._preview_until = time.monotonic() + seconds
        self.camera.show(profile.view(control.view))
        self.overlay.show(control.u, control.v, control.label)

    def close(self) -> None:
        self._stop.set()
        self.overlay.close()
        self.camera.close()

    # -------------------------------------------------------------- état pour l'interface
    def status(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            if not session:
                return {"actif": False, "apercu": time.monotonic() < self._preview_until}
            steps = session.procedure["etapes"]
            step = steps[session.index]
            language = session.language
            return {
                "actif": True, "profil": session.profile.id, "procedure": session.procedure["id"],
                "titre_procedure": text(session.procedure["titre"], language),
                "index": session.index, "total": len(steps), "termine": session.finished,
                "etape": {"id": step["id"], "titre": text(step["titre"], language),
                          "explication": text(step.get("explication"), language),
                          "commande": session.profile.control(step["commande"]).label,
                          "manuelle": self._manual(session.profile, step),
                          "validee": session.done_at is not None},
            }

    # -------------------------------------------------------------- boucle
    def _manual(self, profile: Profile, step: dict[str, Any]) -> bool:
        return bool(step.get("confirmation")) or "attendu" not in step or step["commande"].startswith("EVT_")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:   # pragma: no cover - le guide ne doit jamais mourir
                LOGGER.exception("Guide")
            self._stop.wait(TICK_S)

    def _tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._session is None:
                if self._preview_until and now >= self._preview_until:
                    self._preview_until = 0.0
                    self.overlay.hide()
                    self.camera.release()
                return
            session = self._session
            if session.finished:
                return
            steps = session.procedure["etapes"]
            step = steps[session.index]
            control = session.profile.control(step["commande"])
            if session.shown_index != session.index:
                session.shown_index = session.index
                self.camera.show(session.profile.view(control.view))
                self.overlay.show(control.u, control.v, text(step["titre"], session.language))
            if session.done_at is None and not self._manual(session.profile, step):
                value = self.simulator.snapshot().values.get(step.get("verifier", step["commande"]))
                if satisfied(step["attendu"], value):
                    session.held_since = session.held_since or now
                    if now - session.held_since >= HOLD_S:
                        session.done_at = now
                        self.overlay.mark_done()
                        LOGGER.info("Étape %s validée", step["id"])
                else:
                    session.held_since = None
            if session.done_at is not None and now - session.done_at >= PAUSE_DONE_S:
                if session.index + 1 < len(steps):
                    session.index += 1
                    session.held_since = session.done_at = None
                else:
                    session.finished = True
                    self.overlay.hide()
                    self.camera.release()
                    LOGGER.info("Procédure %s terminée", session.procedure["id"])
