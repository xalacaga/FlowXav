"""« Fais-le pour moi » : FlowXav manœuvre lui-même la commande d'une étape, par le SDK de l'avion.

Toujours sur demande de l'utilisateur (bouton « Fais-le » ou mode automatique qu'il a activé).
Le SDK PMDG accepte une position directe : un événement de commande avec un paramètre
inférieur à 8192 place l'interrupteur ou le sélecteur dans cette position ; les événements
« …_SET » prennent une valeur (vitesse, cap, altitude). Les boutons et touches se pressent
par un clic gauche simulé. FlowXav vérifie ensuite l'état de l'avion ; s'il n'est pas atteint,
l'étape reste à faire à la main.

Ce qui n'est jamais automatisé : leviers carburant, manettes de poussée, volets, speedbrake,
interrupteurs à ressort sans position lisible (GEN, APU GEN, GRD PWR), étapes d'attente.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from flowxav import settings

LOGGER = logging.getLogger(__name__)
MOUSE_LEFT_SINGLE, MOUSE_LEFT_RELEASE = 0x20000000, 0x00020000
POSITION_LIMIT = 8192            # au-delà, le paramètre serait lu comme une action de souris
PAUSE_S = 0.12
CDU_CHARS = {".": "DOT", "/": "SLASH", " ": "SPACE", "-": "PLUS_MINUS", "+": "PLUS_MINUS"}


class NotActionable(Exception):
    """L'étape ne peut pas être faite par FlowXav ; `reason` est un code traduit par l'interface."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Send:
    event: str            # nom EVT_…
    parameter: int
    pause_s: float = PAUSE_S


@dataclass
class Plan:
    sends: list[Send]
    verifies: bool        # True : l'état de l'avion dira si c'est fait ; False : on valide après l'envoi
    field: str | None = None
    value: int | None = None


@lru_cache(maxsize=8)
def _events(path: str) -> dict[str, int]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["evenements"]


def events_of(profile) -> dict[str, int]:
    config = profile.data.get("actions", {})
    return _events(str(profile.folder / config["evenements"])) if config.get("evenements") else {}


def _position(expected: dict[str, Any]) -> int | None:
    """Position à envoyer pour atteindre la condition de l'étape."""
    if "egal" in expected:
        value = expected["egal"]
    elif expected.get("parmi"):
        value = expected["parmi"][0]
    else:
        return None
    if isinstance(value, bool):
        return int(value)
    return value if isinstance(value, int) and 0 <= value < POSITION_LIMIT else None


def _inverted(profile, field: str) -> bool:
    return field in settings.section("actions_inversees").get(profile.id, [])


def remember_inversion(profile, field: str) -> None:
    """Interrupteur dont la position SDK est l'inverse de l'état publié : retenu pour la suite."""
    known = settings.section("actions_inversees")
    fields = sorted(set(known.get(profile.id, [])) | {field})
    settings.update("actions_inversees", {profile.id: fields})


def press(event: str) -> list[Send]:
    return [Send(event, MOUSE_LEFT_SINGLE, 0.05), Send(event, MOUSE_LEFT_RELEASE)]


def plan_step(profile, step: dict[str, Any], context: dict[str, Any], values: dict[str, Any],
              key: str | None = None) -> Plan:
    """Ce qu'il faut envoyer pour faire l'étape, ou NotActionable."""
    from flowxav.guide import fill, value_check      # évite l'import circulaire

    config = profile.data.get("actions")
    if not config:
        raise NotActionable("non_pris_en_charge")
    from flowxav import licence
    if not licence.accepted(profile):
        raise NotActionable("licence")             # contrat de l'éditeur non accepté : on ne pilote pas l'avion
    events = events_of(profile)
    command = key or step.get("commande")
    if not command or step.get("sans_cible"):
        raise NotActionable("sans_commande")

    # Boutons et touches : clic gauche simulé (et saisie préalable dans le bloc-notes du CDU).
    if command.startswith("EVT_"):
        if not any(command.startswith(prefix) for prefix in config.get("boutons", [])):
            raise NotActionable("au_pilote")
        if command not in events:
            raise NotActionable("evenement_inconnu")
        action = step.get("action", {})
        sends: list[Send] = []
        if action.get("saisie"):
            typed = fill(str(action["saisie"]), context)
            if not typed:
                raise NotActionable("valeur_inconnue")
            if not command.startswith("EVT_CDU_L_"):
                raise NotActionable("au_pilote")
            prefix = "EVT_CDU_L_"
            sends += press(f"{prefix}CLR")
            for char in typed.upper():
                name = f"{prefix}{CDU_CHARS.get(char, char)}"
                if name not in events:
                    raise NotActionable("caractere_impossible")
                sends += press(name)
        for _ in range(int(action.get("appuis", 1))):
            sends += press(command)
            sends[-1] = Send(command, MOUSE_LEFT_RELEASE, 0.5)   # laisse le CDU répondre entre deux appuis
        return Plan(sends, verifies=bool(step.get("verifier") and "attendu" in step))

    entry = config.get("commandes", {}).get(command)
    if not entry or entry["evenement"] not in events:
        raise NotActionable("au_pilote")
    if step.get("attente") and not step.get("verifier"):
        raise NotActionable("attente")              # ex. relâchement du démarreur : surtout ne rien envoyer

    # Valeur du vol (vitesse, cap, altitudes) par un événement « SET ».
    if entry.get("regle") == "valeur":
        check = value_check(step, context)
        if not check or "proche" not in check[1]:
            raise NotActionable("valeur_inconnue")
        return Plan([Send(entry["evenement"], int(round(check[1]["proche"])))], verifies=True,
                    field=check[0], value=int(round(check[1]["proche"])))

    # Interrupteur ou sélecteur : position directe.
    if "verifier" in step and "attendu" in step and step["verifier"] != command:
        position = entry.get("valeur_si_verifier")
    else:
        position = _position(step.get("attendu", {}))
    if position is None:
        raise NotActionable("au_pilote")
    if isinstance(values.get(command), bool) and _inverted(profile, command):
        position = 1 - position
    sends = []
    if entry.get("garde"):
        sends.append(Send(entry["garde"], 1, 0.3))   # soulever le cache
    sends.append(Send(entry["evenement"], position, 0.4))
    if entry.get("garde"):
        sends.append(Send(entry["garde"], 0, 0.2))   # le refermer
    return Plan(sends, verifies=True, field=command, value=position)


def execute(client, profile, plan: Plan) -> None:
    """Envoie les commandes du plan, dans l'ordre, au rythme d'un pilote."""
    events = events_of(profile)
    for send in plan.sends:
        client.transmit_event(f"#{events[send.event]}", send.parameter)
        time.sleep(send.pause_s)
    LOGGER.info("Action envoyée : %s", ", ".join(f"{s.event}={s.parameter}" for s in plan.sends[:6]))
