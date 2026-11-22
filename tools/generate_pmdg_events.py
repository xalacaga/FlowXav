"""Diagnostic : table des événements de commande PMDG (nom → numéro) depuis l'en-tête C du SDK.

    python tools/generate_pmdg_events.py <en-tête.h> <sortie.json>

FlowXav n'en a plus besoin : il tire cette table de l'en-tête installé chez l'utilisateur,
au premier lancement (flowxav.sdk). Ne versionnez pas le résultat (contrat PMDG § 1.n et 1.p).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowxav.sdk import parse_events  # noqa: E402

if __name__ == "__main__":
    header, out = Path(sys.argv[1]), Path(sys.argv[2])
    events = parse_events(header.read_text(encoding="latin-1"))
    out.write_text(json.dumps({"source": header.name, "evenements": events}, indent=1), encoding="utf-8")
    print(len(events), "événements ->", out)
