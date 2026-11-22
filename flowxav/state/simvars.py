"""État par SimVars : la source générique des avions qui suivent les variables standard de MSFS.

La configuration associe chaque identifiant de la carte à une SimVar et son
unité, par exemple `{"BAT": ["ELECTRICAL MASTER BATTERY", "Bool"]}`. Les L:vars
propres à un avion s'écrivent `L:nom` avec l'unité `Number`.
"""

from __future__ import annotations

from typing import Any


class SimVarSource:
    name = "SimVars"

    def __init__(self, variables: dict[str, list[str]]) -> None:
        self.mapping = {key: (simvar, unit) for key, (simvar, unit) in variables.items()}

    def read(self, client) -> dict[str, Any]:
        values = client.read_simvars(list(self.mapping.values()), timeout_s=1.0)
        return {key: values[simvar] for key, (simvar, _unit) in self.mapping.items()}

    def help(self) -> str:
        return ""
