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

from flowxav import actions, settings
from flowxav.aircraft import Profile, load_profile
from flowxav.overlay import Overlay
from flowxav.sim.camera import CameraDirector
from flowxav.simulator import Simulator, flight_phase

LOGGER = logging.getLogger(__name__)
HOLD_S = 0.8          # l'état attendu doit tenir ce temps (les barres électriques suivent avec retard)
PAUSE_DONE_S = 1.5    # l'anneau reste vert avant l'étape suivante
BRIEF_S = 3.0         # « camera »: « breve » : la vue est rendue au pilote après ce délai (phases où il pilote)
TICK_S = 0.2
RESUME_MAX_S = 12 * 3600   # guidage interrompu (FlowXav fermé) repris pour le même avion dans ce délai


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
    if "proche" in expected:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        gap = abs(float(value) - expected["proche"])
        if expected.get("angle"):
            gap = min(gap % 360, 360 - gap % 360)          # 359° et 001° sont voisins
        return gap <= expected.get("tolerance", 0) + 1e-6
    return True


# Valeurs du vol en direct (données FMC publiées par l'avion) ; le plan SimBrief complète.
LIVE_VALUES = {"v1": "FMC_V1", "vr": "FMC_VR", "v2": "FMC_V2", "volets": "FMC_TakeoffFlaps"}


def flight_values(values: dict[str, Any]) -> dict[str, Any]:
    """Valeurs de CE vol pour les étapes : plan SimBrief retenu + données FMC en direct."""
    context = {k: v for k, v in settings.section("simbrief").get("dernier_plan", {}).items() if v not in ("", None)}
    for name, key in LIVE_VALUES.items():
        value = values.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            context[name] = int(value)
    runway = str(context.get("piste_depart", ""))
    digits = "".join(ch for ch in runway if ch.isdigit())
    if digits and 1 <= int(digits) <= 36:
        context["cap_piste"] = int(digits) * 10            # cap approché de la piste (32L → 320°)
    return context


def fill(template: str, context: dict[str, Any]) -> str | None:
    """Remplace {nom} par la valeur du vol ; None si une valeur manque."""
    try:
        return template.format_map(context)
    except (KeyError, IndexError, ValueError):
        return None


def value_check(step: dict[str, Any], context: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(champ, condition) qui valide l'étape : « attendu » fixe, ou « valeur » du vol si elle est connue."""
    if "attendu" in step:
        key = step.get("verifier") or step.get("commande")
        return (key, step["attendu"]) if key else None
    target = step.get("valeur", {})
    if not target.get("cle") or "egal" not in target:
        return None
    raw = fill(str(target["egal"]), context)
    try:
        expected = float(raw) if raw is not None else None
    except ValueError:
        expected = None
    if expected is None:
        return None
    tolerance = float(target.get("tolerance", 0))
    return target["cle"], {"proche": expected, "tolerance": tolerance, "angle": bool(target.get("angle"))}


def is_manual(step: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
    """Étape validée par « Fait » : confirmation demandée, rien à contrôler, ou commande EVT_… sans « verifier ».

    Une étape à « valeur » du vol se contrôle seule quand la valeur est connue (plan SimBrief, FMC),
    sinon elle redevient manuelle.
    """
    if step.get("confirmation"):
        return True
    if "attendu" in step:
        return str(step.get("commande", "")).startswith("EVT_") and "verifier" not in step
    if step.get("valeur", {}).get("cle"):
        return context is not None and value_check(step, context) is None
    return True


def applicable(step: dict[str, Any], values: dict[str, Any]) -> bool:
    """« si » : {"cle", "attendu"} — l'étape ne concerne que ce cas (par exemple GSX en marche)."""
    condition = step.get("si")
    return condition is None or satisfied(condition["attendu"], values.get(condition["cle"]))


def step_satisfied(step: dict[str, Any], values: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
    """L'état de l'avion montre déjà cette étape faite (jamais vrai pour une étape manuelle).

    Une étape qui ne s'applique pas (« si » non rempli) compte comme faite : elle est passée.
    """
    if not applicable(step, values):
        return True
    if is_manual(step):
        return False
    check = value_check(step, context if context is not None else flight_values(values))
    return bool(check) and satisfied(check[1], values.get(check[0]))


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
    action: str = ""                    # « Fais-le » : "", "en_cours", "fait", "echec"
    auto_done_index: int = -1           # mode automatique : étape déjà lancée (pas deux fois)


class Guide:
    def __init__(self, simulator: Simulator, camera: CameraDirector | None = None, overlay: Overlay | None = None):
        self.simulator = simulator
        # Par défaut la vue reste au pilote ; « camera.cadrage » = true la place d'abord face au panneau.
        self.camera = camera or CameraDirector(frame=bool(settings.section("camera").get("cadrage", False)))
        self.overlay = overlay or Overlay()
        self._session: Session | None = None
        self._preview_until = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="flowxav-guide", daemon=True)
        self.auto = False               # mode automatique : FlowXav fait lui-même les étapes faisables
        self._resume_checked = False    # reprise du guidage enregistré : tentée une fois par lancement
        self._saved_progress: tuple | None = None
        self.action_client_factory = None   # client SimConnect des actions (remplaçable en test)

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
            self._resume_checked = True        # un guidage choisi remplace toute reprise automatique
        LOGGER.info("Procédure %s démarrée sur %s à l'étape %d", procedure_id, profile.id, start + 1)

    def confirm(self) -> None:
        """« Fait » : valide l'étape en cours, seulement si elle est manuelle.

        Une étape vérifiable attend l'état de l'avion : la valider à la main laisserait
        croire qu'elle est faite (vécu : APU validé sans avoir démarré). « Suivante »
        reste là pour passer outre volontairement.
        """
        with self._lock:
            session = self._session
            if session and not session.finished and session.done_at is None \
                    and self._manual(session.profile, session.procedure["etapes"][session.index]):
                self._session.done_at = time.monotonic()
                self.overlay.mark_done()

    def set_auto(self, active: bool) -> None:
        with self._lock:
            self.auto = bool(active)
            if self._session:
                self._session.auto_done_index = -1
        LOGGER.info("Mode automatique %s", "activé" if active else "désactivé")

    def _plan(self, session: Session, step: dict[str, Any]) -> actions.Plan:
        values = self.simulator.snapshot().values
        return actions.plan_step(session.profile, step, flight_values(values), values,
                                 session.shown_control or step.get("commande"))

    def do_step(self) -> dict[str, Any]:
        """« Fais-le pour moi » : FlowXav manœuvre la commande de l'étape en cours."""
        with self._lock:
            session = self._session
            if not session or session.finished or session.done_at is not None or session.action == "en_cours":
                return {"ok": False, "raison": "rien_a_faire"}
            step = session.procedure["etapes"][session.index]
            try:
                plan = self._plan(session, step)
            except actions.NotActionable as error:
                return {"ok": False, "raison": error.reason}
            session.action = "en_cours"
            index = session.index
        threading.Thread(target=self._perform, args=(session, index, step, plan),
                         name="flowxav-action", daemon=True).start()
        return {"ok": True}

    def _perform(self, session: Session, index: int, step: dict[str, Any], plan: actions.Plan) -> None:
        from flowxav.sim.simconnect import SimConnectClient, SimConnectError
        factory = self.action_client_factory or (lambda: SimConnectClient("FlowXav actions"))
        try:
            with factory() as client:
                actions.execute(client, session.profile, plan)
                if plan.verifies and not self._wait_done(session, index, 4.0) and self._retry_inverted(session, plan):
                    actions.execute(client, session.profile, self._inverted_plan(plan))
                    self._wait_done(session, index, 4.0)
        except (SimConnectError, OSError) as error:
            LOGGER.warning("Action impossible : %s", error)
        with self._lock:
            if self._session is not session or session.index != index:
                return
            if not plan.verifies and session.done_at is None:
                session.done_at = time.monotonic()       # bouton ou touche sans état lisible : envoyé = fait
                self.overlay.mark_done()
            session.action = "fait" if session.done_at is not None else "echec"
            LOGGER.info("Étape %s : action %s", step["id"], session.action)

    def _wait_done(self, session: Session, index: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._session is not session or session.index != index or session.done_at is not None:
                    return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _retry_inverted(session: Session, plan: actions.Plan) -> bool:
        """Interrupteur booléen resté à l'opposé : sa position SDK est inversée, on le retient et on corrige."""
        if plan.field and plan.value in (0, 1) and not plan.field.startswith(("MCP_IAS", "MCP_Heading")):
            actions.remember_inversion(session.profile, plan.field)
            return True
        return False

    @staticmethod
    def _inverted_plan(plan: actions.Plan) -> actions.Plan:
        sends = [actions.Send(s.event, 1 - s.parameter, s.pause_s) if s.parameter == plan.value and "GUARD" not in s.event
                 else s for s in plan.sends]
        return actions.Plan(sends, plan.verifies, plan.field, 1 - plan.value)

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
            self._forget_progress()
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
        self.overlay.show(control.u, control.v, control.label, view, self._current_pose,
                          profile.control_depth(control.key))

    def _current_pose(self):
        """Vue réellement affichée par MSFS, pour garder l'anneau sur la commande."""
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
                "index": session.index, "total": len(steps), "termine": session.finished, "auto": self.auto,
                "etape": {"id": step["id"], "titre": text(step["titre"], language),
                          "explication": text(step.get("explication"), language),
                          "commande": (session.profile.control(session.shown_control or step.get("commande")).label
                                       if session.shown_control or step.get("commande") else ""),
                          "manuelle": self._manual(session.profile, step),
                          "cible": self._target_text(step, language),
                          "faisable": self._actionable(session, step),
                          "action": session.action,
                          "validee": session.done_at is not None},
                "suivantes": [text(s["titre"], language) for s in steps[session.index + 1:session.index + 4]],
                "attente": self._wait_status(session, step, language),
                "alertes": self._alerts(step, language) if session.done_at is None and not session.finished else [],
            }

    def _actionable(self, session: Session, step: dict[str, Any]) -> bool:
        if session.done_at is not None or session.finished:
            return False
        try:
            self._plan(session, step)
            return True
        except actions.NotActionable:
            return False

    def _target_text(self, step: dict[str, Any], language: str) -> str:
        """Valeur de CE vol à afficher sur l'étape (« V2 = 162 kt »), vide si elle n'est pas connue."""
        template = step.get("valeur", {}).get("texte")
        if not template:
            return ""
        return fill(text(template, language), flight_values(self.simulator.snapshot().values)) or ""

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
        return is_manual(step, flight_values(self.simulator.snapshot().values))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:   # pragma: no cover - le guide ne doit jamais mourir
                LOGGER.exception("Guide")
            self._stop.wait(TICK_S)

    # -------------------------------------------------------------- reprise après relance de FlowXav
    def _save_progress(self, session: Session) -> None:
        """Procédure et étape en cours, pour reprendre là si FlowXav est fermé puis relancé."""
        key = (session.profile.id, session.procedure["id"], session.index, session.finished)
        if key == self._saved_progress:
            return
        self._saved_progress = key
        snapshot = self.simulator.snapshot()
        try:
            if session.finished:
                settings.update("reprise", {"procedure": ""})
            else:
                settings.update("reprise", {"profil": session.profile.id, "procedure": session.procedure["id"],
                                            "index": session.index, "langue": session.language,
                                            "avion": snapshot.title, "horodatage": time.time()})
        except OSError:
            LOGGER.warning("Progression du guidage non enregistrée")

    def _forget_progress(self) -> None:
        self._saved_progress = None
        try:
            settings.update("reprise", {"procedure": ""})
        except OSError:
            LOGGER.warning("Progression du guidage non effacée")

    def _resume(self) -> None:
        """Au lancement : reprend le guidage interrompu, à l'étape la plus avancée entre celle
        enregistrée et celle que l'état de l'avion permet de déduire."""
        snapshot = self.simulator.snapshot()
        if not (snapshot.connected and snapshot.profile_id and snapshot.source_ok and snapshot.values):
            return                                          # avion pas encore lu : on réessaie au prochain tour
        self._resume_checked = True
        saved = settings.section("reprise")
        age = time.time() - float(saved.get("horodatage", 0) or 0)
        if not saved.get("procedure") or saved.get("profil") != snapshot.profile_id                 or saved.get("avion") != snapshot.title or not 0 <= age < RESUME_MAX_S:
            return
        try:
            profile = load_profile(snapshot.profile_id)
            procedure = profile.procedure(saved["procedure"])
        except (KeyError, ValueError):
            return
        self.start(profile, procedure["id"], str(saved.get("langue", "fr")))
        session = self._session
        if session is not None:
            saved_index = int(saved.get("index", 0))
            session.index = max(session.index, min(saved_index, len(procedure["etapes"]) - 1))
            LOGGER.info("Guidage repris : %s, étape %d", procedure["id"], session.index + 1)

    def _tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._session is None and not self._resume_checked:
                self._resume()
            if self._session is not None:
                self._save_progress(self._session)
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
                context = flight_values(values)
                while session.index != session.pinned and step_satisfied(steps[session.index], values, context):
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
                                  session.profile.view(control.view), self._current_pose,
                                  session.profile.control_depth(control.key))
            if session.shown_index != session.index:
                session.wait_started = None
                session.shown_control = key
                session.shown_index = session.index
                session.action = ""
                auto = self.auto and session.auto_done_index != session.index and self._actionable(session, step)
                if auto:
                    # Mode automatique : FlowXav fait l'étape lui-même, sans prendre la caméra.
                    session.auto_done_index = session.index
                    self.overlay.hide()
                    self.camera.release()
                    threading.Thread(target=self.do_step, name="flowxav-auto", daemon=True).start()
                    control = None
                elif control:
                    view = session.profile.view(control.view)
                    self.camera.show(view)
                    self.overlay.show(control.u, control.v, text(step["titre"], session.language), view,
                                      self._current_pose, session.profile.control_depth(control.key))
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
                    # Rien à faire pendant l'attente : la vue est rendue au pilote, l'anneau suit sa vue réelle.
                    free = getattr(self.camera, "free", None)
                    if callable(free):
                        free()
                check = value_check(step, flight_values(values))
                if check and satisfied(check[1], values.get(check[0])):
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
