"""Sources d'état de l'avion : chaque profil nomme la sienne.

Une source sait lire, d'un coup, l'état de toutes les commandes qu'elle connaît
et le renvoie sous forme de dictionnaire `{identifiant: valeur}`. Les
identifiants sont ceux de la carte du profil (ex. « ELEC_BatSelector »,
« IRS_ModeSelector[0] »). Le moteur de guidage n'en sait pas plus.
"""

from __future__ import annotations

from typing import Any, Protocol


class StateSource(Protocol):
    name: str

    def read(self, client) -> dict[str, Any]:
        """Lit l'état courant ; lève SimConnectError si la source est indisponible."""

    def help(self) -> str:
        """Ce que l'utilisateur doit faire pour rendre la source disponible."""


def make_source(config: dict[str, Any], profile_dir) -> StateSource:
    kind = config["source"]
    if kind == "pmdg_ng3":
        from flowxav.state.pmdg import PmdgClientData
        return PmdgClientData(profile_dir / config["disposition"], config["zone"], config.get("aide", ""))
    if kind == "simvars":
        from flowxav.state.simvars import SimVarSource
        return SimVarSource(config["variables"])
    raise ValueError(f"Source d'état inconnue : {kind}")
