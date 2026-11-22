"""Relève la vue actuelle du joueur dans MSFS et l'écrit dans un fichier de vue.

    python tools/cartographie/record_view.py <avion> <nom_de_vue>
    -> tools/cartographie/<avion>/pose_<nom_de_vue>.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flowxav.sim.simconnect import SimConnectClient  # noqa: E402

aircraft, name = sys.argv[1], sys.argv[2]
with SimConnectClient("FlowXav cartographie") as client:
    pose = client.camera_get()
out = Path(__file__).parent / aircraft / f"pose_{name}.json"
out.write_text(json.dumps({"pose": pose.to_dict()}, indent=1), encoding="utf-8")
print("vue relevée :", pose.to_dict(), "->", out)
