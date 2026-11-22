"""Diagnostic : disposition d'une zone de données PMDG depuis l'en-tête C de son SDK.

    python tools/generate_pmdg_layout.py <en-tête.h> <structure> <sortie.json>

FlowXav n'en a plus besoin : il tire cette disposition de l'en-tête installé chez l'utilisateur,
au premier lancement (flowxav.sdk). Ne versionnez pas le résultat (contrat PMDG § 1.n et 1.p).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowxav.sdk import parse_layout  # noqa: E402

if __name__ == "__main__":
    result = parse_layout(Path(sys.argv[1]).read_text(encoding="latin-1"), sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"{len(result['champs'])} champs, {result['taille']} octets -> {sys.argv[3]}")
