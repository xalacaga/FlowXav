"""Moteur de guidage : enchaîne les étapes d'une procédure sur le cockpit réel.

Pour chaque étape : la caméra prend la vue de la commande, l'anneau clignote
dessus, et FlowXav attend que l'état de l'avion corresponde à ce qui est
attendu. L'anneau passe au vert, puis l'étape suivante commence.

Une étape sans état lisible (bouton-poussoir, commande EVT_…) se valide par
le bouton « Fait » de l'interface, sauf si elle désigne avec « verifier » un
autre champ qui traduit son effet (voyant, donnée FMC…).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from flowxav import settings
from flowxav.aircraft import Profile
from flowxav.overlay import Overlay
from flowxav.sim.camera import CameraDirector
from flowxav.simulator import Simulator, flight_phase

LOGGER = logging.getLogger(__name__)
HOLD_S = 0.8          # l'état attendu doit tenir ce temps (les barres électriques suivent avec retard)
PAUSE_DONE_S = 1.5    # l'anneau reste vert avant l'étape suivante
BRIEF_S = 3.0         # « camera »: « breve » : la vue est rendue au pilote après ce délai (phases où il pilote)
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


def is_manual(step: dict[str, Any]) -> bool:
    """Étape validée par « Fait » : sans condition, confirmation demandée, ou commande EVT_… sans « verifier »."""
    if step.get("confirmation") or "attendu" not in step:
        return True
    return str(step.get("commande", "")).startswith("EVT_") and "verifier" not in step


def step_satisfied(step: dict[str, Any], values: dict[str, Any]) -> bool:
    """L'état de l'avion montre déjà cette étape faite (jamais vrai pour une étape manuelle)."""
    key = step.get("verifier") or step.get("commande")
    return not is_manual(step) and bool(key) and satisfied(step["attendu"], values.get(key))


def resume_index(procedure: dict[str, Any], values: dict[str, Any]) -> int:
    """Où reprendre : juste après la plus longue suite d'étapes vérifiables déjà conformes.

    Les étapes « Fait » situées avant la dernière étape conforme de cette suite sont
    considérées comme faites, puisque l'avion est allé au-delà.
    """
    resume = 0
    for index, step in enumerate(procedure["etapes"]):
        if is_manual(step):
            continue
        if not step_satisfied(step, values):
            break
        resume = index + 1
    return resume


def is_complete(procedure: dict[str, Any], values: dict[str, Any]) -> bool:
    steps = procedure["etapes"]
    return any(not is_manual(s) for s in steps) and resume_index(procedure, values) >= max(
        i + 1 for i, s in enumerate(steps) if not is_manual(s))


def phases(procedures: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Procédures du parcours regroupées par phase (variantes : APU ou GPU…), dans l'ordre des fichiers."""
    groups: dict[Any, list[dict[str, Any]]] = {}
    for index, procedure in enumerate(p for p in procedures if not p.get("demonstration")):
        groups.setdefault(procedure.get("phase", f"#{index}"), []).append(procedure)
    return list(groups.values())


def progress(procedure: dict[str, Any], values: dict[str, Any]) -> float:
    steps = len(procedure["etapes"]) or 1
    return resume_index(procedure, values) / steps


def suggest(procedures: list[dict[str, Any]], values: dict[str, Any]) -> str | None:
    """Procédure à proposer : la phase qui suit la dernière accomplie, dans sa variante la plus avancée."""
    groups = phases(procedures)
    if not groups or not values:
        return None
    current_phase = flight_phase(values)
    active = [group for group in groups if any(current_phase in p.get("phases_vol", []) for p in group)]
    if active:
        group = next((candidate for candidate in active
                      if not any(is_complete(procedure, values) for procedure in candidate)), active[-1])
        return max(group, key=lambda p: progress(p, values))["id"]
    done = [i for i, group in enumerate(groups) if any(is_complete(p, values) for p in group)]
    group = groups[min(done[-1] + 1, len(groups) - 1) if done else 0]
    return max(group, key=lambda p: progress(p, values))["id"]   # à égalité : la première variante


def journey(procedures: list[dict[str, Any]], values: dict[str, Any]) -> dict[str, str]:
    """Statut de chaque procédure pour le parcours : « fait », « propose » ou « a_faire » (par phase)."""
    proposal = suggest(procedures, values)
    status: dict[str, str] = {}
    seen = False
    for group in phases(procedures):
        ids = [p["id"] for p in group]
        if proposal in ids:
            label, seen = "propose", True
        elif not seen and proposal is not None:
            label = "fait"
        else:
            label = "a_faire"
        status.update({procedure_id: label for procedure_id in ids})
    return status


def expected_duration(profile_id: str, step: dict[str, Any]) -> float:
    """Durée prévue d'une attente : celle constatée dans cet avion, sinon celle de la procédure."""
    learned = settings.section("durees").get(f"{profile_id}/{step['id']}")
    return float(learned) if isinstance(learned, (int, float)) and learned > 0 else float(step["attente"]["duree_s"])


def learn_duration(profile_id: str, step_id: str, seconds: float) -> None:
    """Retient la durée réelle (moyenne glissante) pour que le prochain compte à rebours soit juste."""
    if not 3 <= seconds <= 1800:
        return
    key = f"{profile_id}/{step_id}"
    previous = settings.section("durees").get(key)
    value = seconds if not isinstance(previous, (int, float)) else 0.5 * previous + 0.5 * seconds
    try:
        settings.update("durees", {key: round(value, 1)})
    except OSError:
        LOGGER.warning("Durée de %s non enregistrée", step_id)


def cdu_target(profile: Profile, step: dict[str, Any], lines: list[str]) -> str | None:
    """Commande à montrer d'après l'écran du CDU : la touche en face du texte attendu.

    « cible_cdu » : {"texte": "LOAD"} ou {"texte": "{piste_depart}"}, les champs entre
    accolades venant du dernier plan SimBrief importé. None si rien ne correspond.
    """
    target = step.get("cible_cdu")
    config = profile.data.get("cdu")
    if not target or not config or not lines:
        return None
    from flowxav.state.pmdg_cdu import find_key
    plan = settings.section("simbrief").get("dernier_plan", {})
    try:
        wanted = target["texte"].format(**plan).strip()
    except (KeyError, IndexError, ValueError):
        return None
    if not wanted:
        return None
    key = find_key(lines, wanted, whole_word=target.get("mot_entier", True))
    control = f"{config['touches']}{key}" if key else None
    return control if control and control in profile.carte["commandes"] else None


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
    pinned: int = -1          # étape choisie avec Précédente/Suivante : jamais sautée d'office
    wait_started: float | None = None   # début constaté de l'attente de l'étape (compte à rebours)
    shown_control: str | None = None    # commande où l'anneau est posé (peut suivre l'écran du CDU)
    camera_until: float | None = None   # caméra brève : instant où la vue est rendue au pilote


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
            procedure = profile.procedure(procedure_id)
            values = self.simulator.snapshot().values
            start = min(resume_index(procedure, values), len(procedure["etapes"]) - 1)
            self._session = Session(profile, procedure, language, index=start)
            self._preview_until = 0.0
        LOGGER.info("Procédure %s démarrée sur %s à l'étape %d", procedure_id, profile.id, start + 1)

    def confirm(self) -> None:
        """« Fait » : valide l'étape en cours, seulement si elle est manuelle.

        Une étape vérifiable attend l'état de l'avion : la valider à la main laisserait
        croire qu'elle est faite (vécu : APU validé sans avoir démarré). « Suivante »
        reste là pour passer outre volontairement.
        """
        with self._lock:
            session = self._session
            if session and not session.finished and session.done_at is None                     and is_manual(session.procedure["etapes"][session.index]):
                self._session.done_at = time.monotonic()
                self.overlay.mark_done()

    def go(self, delta: int) -> None:
        with self._lock:
            session = self._session
            if session:
                session.index = max(0, min(len(session.procedure["etapes"]) - 1, session.index + delta))
                session.held_since = session.done_at = None
                session.shown_index, session.finished = -1, False
                session.pinned = session.index

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
        view = profile.view(control.view)
        self.camera.show(view)
        self.overlay.show(control.u, control.v, control.label, view, self._current_pose)

    def _current_pose(self):
        """Vue réellement affichée (regard libre compris), pour garder l'anneau sur la commande."""
        current = getattr(self.camera, "current_pose", None)
        return current() if callable(current) else None

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
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
                          "commande": (session.profile.control(session.shown_control or step.get("commande")).label
                                       if session.shown_control or step.get("commande") else ""),
                          "manuelle": self._manual(session.profile, step),
                          "validee": session.done_at is not None},
                "suivantes": [text(s["titre"], language) for s in steps[session.index + 1:session.index + 4]],
                "attente": self._wait_status(session, step, language),
                "alertes": self._alerts(step, language) if session.done_at is None and not session.finished else [],
            }

    def _alerts(self, step: dict[str, Any], language: str) -> list[str]:
        """Messages des alertes de l'étape dont la condition est vraie dans l'avion (ex. APU FAULT)."""
        values = self.simulator.snapshot().values
        return [text(alert["message"], language) for alert in step.get("alertes", [])
                if satisfied(alert["attendu"], values.get(alert["cle"]))]

    def _wait_status(self, session: Session, step: dict[str, Any], language: str) -> dict[str, Any] | None:
        """Compte à rebours de l'étape : durée prévue, temps écoulé (None tant que rien n'a commencé)."""
        wait = step.get("attente")
        if not wait or session.done_at is not None or session.finished:
            return None
        elapsed = None if session.wait_started is None else round(time.monotonic() - session.wait_started, 1)
        return {"libelle": text(wait.get("libelle"), language), "duree": expected_duration(session.profile.id, step),
                "ecoule": elapsed, "debut": text(wait.get("debut", {}).get("conseil"), language)}

    # -------------------------------------------------------------- boucle
    def _manual(self, profile: Profile, step: dict[str, Any]) -> bool:
        return is_manual(step)

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
            if session.shown_index != session.index and session.done_at is None:
                # Étapes déjà faites dans l'avion : on les passe sans s'y arrêter.
                values = self.simulator.snapshot().values
                while session.index != session.pinned and step_satisfied(steps[session.index], values):
                    LOGGER.info("Étape %s déjà faite", steps[session.index]["id"])
                    if session.index + 1 >= len(steps):
                        session.finished = True
                        self.overlay.hide()
                        self.camera.release()
                        return
                    session.index += 1
            if session.camera_until is not None and now >= session.camera_until:
                session.camera_until = None
                self.overlay.hide()
                self.camera.release()
            step = steps[session.index]
            base_key = step.get("commande")
            key = (cdu_target(session.profile, step, self.simulator.snapshot().cdu) or base_key) if base_key else None
            control = session.profile.control(key) if key else None
            if control and session.shown_index == session.index and key != session.shown_control:
                # La touche visée a changé sur l'écran du CDU : on déplace l'anneau, pas la caméra.
                session.shown_control = key
                self.overlay.show(control.u, control.v, text(step["titre"], session.language),
                                  session.profile.view(control.view), self._current_pose)
            if session.shown_index != session.index:
                session.wait_started = None
                session.shown_control = key
                session.shown_index = session.index
                if control:
                    view = session.profile.view(control.view)
                    self.camera.show(view)
                    self.overlay.show(control.u, control.v, text(step["titre"], session.language), view, self._current_pose)
                    # En approche ou au roulage après l'atterrissage, le pilote doit garder la vue dehors.
                    session.camera_until = now + BRIEF_S if session.procedure.get("camera") == "breve" else None
                else:
                    self.overlay.hide()
                    self.camera.release()
            if session.done_at is None and not self._manual(session.profile, step):
                values = self.simulator.snapshot().values
                wait = step.get("attente")
                if wait and session.wait_started is None and satisfied(wait["debut"]["attendu"], values.get(wait["debut"]["cle"])):
                    session.wait_started = now
                    LOGGER.info("Attente de l'étape %s commencée", step["id"])
                value = values.get(step.get("verifier") or step.get("commande"))
                if satisfied(step["attendu"], value):
                    session.held_since = session.held_since or now
                    if now - session.held_since >= HOLD_S:
                        session.done_at = now
                        self.overlay.mark_done()
                        LOGGER.info("Étape %s validée", step["id"])
                        if wait and session.wait_started is not None:
                            learn_duration(session.profile.id, step["id"], now - session.wait_started)
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
