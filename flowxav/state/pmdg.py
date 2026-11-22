"""État des avions PMDG par leur SDK : une zone ClientData décrite par une disposition.

La disposition (nom, décalage, type, nombre) est générée une fois depuis
l'en-tête C du SDK livré avec l'avion (tools/generate_pmdg_layout.py) et
voyage avec FlowXav. La diffusion doit être activée dans le fichier d'options
de l'avion (`[SDK]` puis `EnableDataBroadcast=1`).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

FORMATS = {"bool": "?", "char": "c", "unsigned char": "B", "short": "h", "unsigned short": "H",
           "int": "i", "unsigned int": "I", "float": "f", "double": "d"}


class PmdgClientData:
    def __init__(self, layout_path: Path, area: str, help_text: str = "") -> None:
        layout = json.loads(Path(layout_path).read_text(encoding="utf-8"))
        self.name = f"SDK PMDG ({area})"
        self.area = area
        self.size = layout["taille"]
        self.fields = layout["champs"]
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
