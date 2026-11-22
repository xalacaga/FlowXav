"""Assemble la carte d'un avion : une vue par commande, et le point où la montrer.

    python tools/cartographie/build_map.py pmdg_737_900
    écrit flowxav/aircraft/pmdg_737_900/carte.json

Sources, dans l'ordre de priorité (la première qui connaît une commande l'emporte) :
les séances de clics (vue verrouillée), puis les points repérés à l'image.
La liste des sources de chaque avion est dans <avion>/sources.json.
"""

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
aircraft = sys.argv[1]
folder = Path(__file__).parent / aircraft
config = json.loads((folder / "sources.json").read_text(encoding="utf-8"))
pattern = re.compile(r"\[\s*([\d.]+)s\] (\S+): .* clic \S+ à \(([\d.]+), ([\d.]+)\)")


def keep(key: str) -> bool:
    # Un voyant qui change par conséquence se rattache au clic suivant : jamais fiable.
    return "annun" not in key and key not in config.get("exclus", [])


views = {name: json.loads((folder / file).read_text(encoding="utf-8"))["pose"]
         for name, file in config["vues"].items()}
points_image = json.loads((folder / "points_image.json").read_text(encoding="utf-8"))
controls: dict[str, dict] = {}
for source in config["sources"]:
    view = source["vue"]
    found: dict[str, list[tuple[float, float]]] = {}
    labels: dict[str, str] = {}
    if "image" in source:
        width, height = config["fenetre"]
        for key, label, x, y in points_image[source["image"]]:
            found[key] = [(x / width, y / height)]
            labels[key] = label
    else:
        limit = source.get("avant_s")
        for line in (folder / "seances" / source["seance"]).read_text(encoding="utf-8").splitlines():
            m = pattern.match(line)
            if m and (limit is None or float(m.group(1)) < limit) and keep(m.group(2)):
                found.setdefault(m.group(2), []).append((float(m.group(3)), float(m.group(4))))
    for key, pts in found.items():
        if key not in controls:
            controls[key] = {"vue": view,
                             "u": round(statistics.median(p[0] for p in pts), 4),
                             "v": round(statistics.median(p[1] for p in pts), 4),
                             "libelle": labels.get(key) or config.get("libelles", {}).get(key, key),
                             "origine": "image" if "image" in source else "clics"}
carte = {"avion": aircraft, "fenetre": config["fenetre"], "vues": views, "commandes": controls}
out = ROOT / "flowxav" / "aircraft" / aircraft / "carte.json"
out.write_text(json.dumps(carte, indent=1, ensure_ascii=False), encoding="utf-8")
by_view: dict[str, int] = {}
for c in controls.values():
    by_view[c["vue"]] = by_view.get(c["vue"], 0) + 1
print(by_view, "total", len(controls), "->", out)
