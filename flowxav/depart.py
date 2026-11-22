"""État de départ : remettre l'avion dans un état connu, puis le vérifier.

Remise à zéro (sur clic de l'utilisateur, après confirmation) : FlowXav pilote
le CDU comme le ferait le pilote, MENU > PMDG SETUP > PANEL STATE LOAD, puis
choisit l'état demandé (COLDDARK). Il lit l'écran à chaque page pour trouver
la bonne touche : aucune position n'est supposée.

Vérification : une liste de points (profile.json, « depart ») lus dans l'état
de l'avion et dans les SimVars de MSFS, par exemple les manettes de poussée,
qui suivent la manette physique de l'utilisateur et qu'aucun état chargé ne
remet au ralenti.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from flowxav.guide import satisfied
from flowxav.state.pmdg_cdu import PmdgCdu, find_key, text_of

LOGGER = logging.getLogger(__name__)
MAX_PAGES = 6


class DepartError(Exception):
    """Erreur présentable : `code` est traduit par l'interface ; `ecran` aide au diagnostic."""

    def __init__(self, code: str, screen: list[str] | None = None) -> None:
        super().__init__(code)
        self.code, self.screen = code, text_of(screen or [])


def load_panel_state(cdu: PmdgCdu, state_name: str) -> list[str]:
    """Charge un état de panneau PMDG par le CDU ; renvoie l'écran final."""
    powered, lines = cdu.screen()
    if not powered:
        raise DepartError("cdu_eteint", lines)
    lines = cdu.press_and_wait("MENU")
    key = find_key(lines, "PMDG SETUP")
    if key is None:
        raise DepartError("menu_introuvable", lines)
    lines = cdu.press_and_wait(key)
    key = find_key(lines, "STATE LOAD")
    if key is None:
        raise DepartError("chargement_introuvable", lines)
    lines = cdu.press_and_wait(key)
    for _page in range(MAX_PAGES):
        key = find_key(lines, state_name, whole_word=True)
        if key is not None:
            LOGGER.info("Chargement de l'état PMDG %s (%s)", state_name, key)
            lines = cdu.press_and_wait(key, timeout_s=4.0)
            # Le PMDG demande de confirmer par EXEC (voyant allumé, invite <CANCEL) : constaté en vol.
            if find_key(lines, "CANCEL", whole_word=True) is not None:
                lines = cdu.press_and_wait("EXEC", timeout_s=6.0)
            else:
                confirm = find_key(lines, "CONFIRM") or find_key(lines, "YES", whole_word=True)
                if confirm is not None:
                    lines = cdu.press_and_wait(confirm, timeout_s=4.0)
            return lines
        previous = lines
        lines = cdu.press_and_wait("NEXT_PAGE")
        if lines == previous:
            break
    raise DepartError("etat_introuvable", lines)


def check(config: dict[str, Any], pmdg_values: dict[str, Any], simvar_values: dict[str, float]) -> list[dict[str, Any]]:
    """Chaque point de vérification avec sa valeur lue et son verdict."""
    results = []
    for item in config.get("verifications", []):
        if "simvar" in item:
            value = simvar_values.get(item["simvar"][0])
        else:
            value = pmdg_values.get(item["cle"])
        results.append({"id": item["id"], "libelle": item["libelle"], "conseil": item.get("conseil", {}),
                        "valeur": value, "ok": satisfied(item["attendu"], value)})
    return results


def simvars_of(config: dict[str, Any]) -> list[tuple[str, str]]:
    return [tuple(item["simvar"]) for item in config.get("verifications", []) if "simvar" in item]


def verify(client, profile) -> list[dict[str, Any]]:
    config = profile.data.get("depart", {})
    return check(config, profile.source.read(client), client.read_simvars(simvars_of(config), timeout_s=3.0))


def reset(client, profile, state: str | None = None, settle_s: float = 6.0) -> dict[str, Any]:
    """Remet l'avion dans un état de départ du profil (COLDDARK par défaut, ou COLDDARKGPU…), puis le vérifie."""
    config = profile.data.get("depart")
    if not config:
        raise DepartError("non_pris_en_charge")
    allowed = [s["id"] for s in config.get("etats", [])] or [config["etat_pmdg"]]
    state = state or config["etat_pmdg"]
    if state not in allowed:
        raise DepartError("etat_inconnu")
    from flowxav.actions import events_of
    events = events_of(profile)
    if not events:
        raise DepartError("sdk_absent")
    lines = load_panel_state(PmdgCdu(client, config.get("cdu", "PMDG_NG3_CDU_0"), events), state)
    time.sleep(settle_s)                      # l'avion bascule et ses systèmes s'éteignent
    return {"etat": state, "ecran": text_of(lines), "verifications": verify(client, profile)}
