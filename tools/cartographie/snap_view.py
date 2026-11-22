"""Verrouille une vue, capture la zone client de MSFS en PNG, puis rend la caméra.

    python tools/cartographie/snap_view.py <avion> <nom_de_vue>
    lit  <avion>/pose_<nom>.json, écrit <avion>/captures/captures_<nom>.png
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from flowxav.sim.simconnect import Pose, SimConnectClient  # noqa: E402
from flowxav.sim.window import msfs_client_rect  # noqa: E402
from imaging import grab_rgb, write_png  # noqa: E402

aircraft, name = sys.argv[1], sys.argv[2]
folder = Path(__file__).parent / aircraft
pose = Pose.from_dict(json.loads((folder / f"pose_{name}.json").read_text(encoding="utf-8"))["pose"])
with SimConnectClient("FlowXav cartographie") as client:
    client.camera_acquire()
    try:
        end = time.monotonic() + 2.5          # laisse le rendu se stabiliser
        while time.monotonic() < end:
            client.camera_set(pose)
            for _ in client.messages():
                pass
            time.sleep(0.033)
        left, top, width, height = msfs_client_rect()
        out = folder / "captures" / f"captures_{name}.png"
        out.parent.mkdir(exist_ok=True)
        write_png(out, width, height, grab_rgb(left, top, width, height))
        print(f"capture {width}x{height} -> {out}")
    finally:
        client.camera_release()
