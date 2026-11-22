"""État des avions PMDG par leur SDK : une zone ClientData décrite par une disposition.

La disposition (nom, décalage, type, nombre) est tirée de l'en-tête C du SDK
livré avec l'avion, chez l'utilisateur, au premier lancement (flowxav.sdk) :
FlowXav ne livre aucun fichier de l'éditeur. La diffusion doit être activée
dans le fichier d'options de l'avion (`[SDK]` puis `EnableDataBroadcast=1`).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

FORMATS = {"bool": "?", "char": "c", "unsigned char": "B", "short": "h", "unsigned short": "H",
           "int": "i", "unsigned int": "I", "float": "f", "double": "d"}
MAX_LAYOUT_BYTES = 1024 * 1024


class PmdgClientData:
    def __init__(self, layout: dict[str, Any] | Path, area: str, help_text: str = "") -> None:
        if not isinstance(layout, dict):
            path = Path(layout)
            if path.stat().st_size > MAX_LAYOUT_BYTES:
                raise ValueError("Disposition PMDG trop volumineuse.")
            layout = json.loads(path.read_text(encoding="utf-8"))
        size, fields = layout.get("taille"), layout.get("champs")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_LAYOUT_BYTES:
            raise ValueError("Taille de disposition PMDG invalide.")
        if not isinstance(fields, dict):
            raise ValueError("Champs de disposition PMDG invalides.")
        for name, definition in fields.items():
            if (not isinstance(name, str) or not isinstance(definition, list) or len(definition) != 3):
                raise ValueError("Champ de disposition PMDG invalide.")
            offset, ctype, count = definition
            if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or ctype not in FORMATS
                    or not isinstance(count, int) or isinstance(count, bool) or count <= 0
                    or offset + struct.calcsize(f"<{count}{FORMATS[ctype]}") > size):
                raise ValueError(f"Champ PMDG hors limites : {name}")
        self.name = f"SDK PMDG ({area})"
        self.area = area
        self.size = size
        self.fields = fields
        self._help = help_text

    def decode(self, data: bytes) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, (offset, ctype, count) in self.fields.items():
            if ctype == "char":
                values[name] = data[offset:offset + count].split(b"\0")[0].decode("latin-1")
                continue
            fmt = FORMATS[ctype]
            items = struct.unpack_from(f"<{count}{fmt}", data, offset)
            if count == 1:
                values[name] = items[0]
            else:
                for index, item in enumerate(items):
                    values[f"{name}[{index}]"] = item
        return values

    def read(self, client) -> dict[str, Any]:
        return self.decode(client.read_client_data(self.area, self.size, timeout_s=1.0))

    def help(self) -> str:
        return self._help
