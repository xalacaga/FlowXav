"""Génère la table des événements de commande PMDG (nom → numéro) depuis l'en-tête C du SDK.

    python tools/generate_pmdg_events.py <en-tête.h> <sortie.json>
    ex. : "D:\\MSFS2024\\Community\\pmdg-aircraft-739\\Documentation\\SDK\\PMDG_NG3_SDK.h"
          flowxav/aircraft/pmdg_737_900/pmdg_ng3_events.json

Les événements s'écrivent « #define EVT_X (THIRD_PARTY_EVENT_ID_MIN + n) » ou
renvoient à un autre événement (« #define EVT_Y EVT_X »). Seuls les numéros
sont publiés : l'en-tête lui-même reste sur le poste du développeur.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

THIRD_PARTY_EVENT_ID_MIN = 0x00011000
OFFSET = re.compile(r"^\s*#define\s+(EVT_\w+)\s+\(\s*THIRD_PARTY_EVENT_ID_MIN\s*\+\s*(\d+)\s*\)", re.M)
ALIAS = re.compile(r"^\s*#define\s+(EVT_\w+)\s+(EVT_\w+)\s*$", re.M)


def parse(header: str) -> dict[str, int]:
    events = {name: THIRD_PARTY_EVENT_ID_MIN + int(offset) for name, offset in OFFSET.findall(header)}
    for _ in range(3):                                  # alias d'alias
        for name, target in ALIAS.findall(header):
            if target in events and name not in events:
                events[name] = events[target]
    return dict(sorted(events.items()))


def main() -> int:
    header, out = Path(sys.argv[1]), Path(sys.argv[2])
    events = parse(header.read_text(encoding="utf-8", errors="replace"))
    out.write_text(json.dumps({"source": header.name, "evenements": events}, indent=1), encoding="utf-8")
    print(len(events), "événements ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
