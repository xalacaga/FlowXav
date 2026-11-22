"""Image de contrôle des points repérés sur une capture.

    python tools/cartographie/vision.py <avion> <nom_de_vue>
    lit  <avion>/points_image.json et <avion>/captures/captures_<nom>.png
    écrit <avion>/captures/controle_<nom>.png (un repère numéroté par commande)

Les points (pixels de la capture) sont repérés en lisant les inscriptions du
panneau sur la capture ; cette image sert à les vérifier d'un coup d'œil avant
le contrôle dans le simulateur (« montre-moi »).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from imaging import Canvas, read_png, write_png  # noqa: E402

aircraft, name = sys.argv[1], sys.argv[2]
folder = Path(__file__).parent / aircraft
points = json.loads((folder / "points_image.json").read_text(encoding="utf-8"))[name]
width, height, rgb = read_png(folder / "captures" / f"captures_{name}.png")
canvas = Canvas(width, height, rgb)
for index, (_key, label, x, y) in enumerate(points, 1):
    canvas.ring(x, y, 7, (0, 255, 255))
    canvas.put(x, y, (255, 0, 255))
    canvas.number(x + 9, y - 14, str(index), (255, 255, 0))
    print(index, label)
write_png(folder / "captures" / f"controle_{name}.png", width, height, canvas.rgb)
