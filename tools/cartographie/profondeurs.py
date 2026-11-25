"""Profondeur de chaque commande par stéréo : vue de référence + vues depuis les sièges.

    python tools/cartographie/profondeurs.py <avion> [vue ...] [--sans-capture]

Une fois la caméra rendue, MSFS remet le pilote à sa place : l'œil n'est plus
celui de la vue de référence, et l'anneau ne reste sur la commande que si l'on
connaît sa position dans l'espace. Pour chaque vue, l'outil :

1. tient la vue de référence et la capture, puis capture le même panneau depuis
   le siège du commandant et celui du copilote (la caméra est rendue ensuite) ;
2. pour chaque commande, essaie des profondeurs le long de son rayon : le
   voisinage de la commande (supposé face à la vue) est reprojeté dans les
   captures des sièges, et la profondeur retenue est celle où l'image
   correspond le mieux (corrélation normalisée) ;
3. écrit <avion>/profondeurs.json (relu par build_map) et met à jour la carte
   de l'avion, puis une image de contrôle par vue (anneau vert : profondeur
   retenue ; rouge : trop incertaine, la profondeur de la vue est gardée).

--sans-capture recalcule à partir des captures déjà présentes.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from flowxav.aircraft import load_profile  # noqa: E402
from flowxav.sim.projection import _eye, direction, project  # noqa: E402
from flowxav.sim.simconnect import Pose  # noqa: E402
from imaging import Canvas, read_png, write_png  # noqa: E402

# Œil du pilote une fois la caméra rendue (relevé en vol, repère MSFS : x positif vers la GAUCHE).
SEATS = {"commandant": (0.505, 1.555, 15.402), "copilote": (-0.505, 1.555, 15.402)}
SPAN = (-0.25, 0.35)        # profondeurs essayées autour de celle du panneau : hors de là, ce serait un faux appariement
PATCH, STEP = 40, 4                                               # voisinage ±40 px, un pixel sur quatre
MIN_SCORE, MIN_CONTRAST = 0.6, 6.0
USED_SEATS = ("commandant",)   # la vue du pilote ; celle du copilote est trop oblique pour la corrélation


def gray(width: int, height: int, rgb: bytearray) -> list[float]:
    return [0.299 * rgb[i] + 0.587 * rgb[i + 1] + 0.114 * rgb[i + 2] for i in range(0, width * height * 3, 3)]


def seat_pose(reference: Pose, seat: tuple[float, float, float], aspect: float, depth: float) -> Pose:
    """Vue depuis un siège, pointée sur le centre du panneau de la vue de référence."""
    ex, ey, ez = _eye(reference)
    ax, ay, az = direction(reference, 0.5, 0.5, aspect)
    target = (ex + ax * depth, ey + ay * depth, ez + az * depth)
    sx, sy, sz = -seat[0], seat[1], seat[2]
    dx, dy, dz = target[0] - sx, target[1] - sy, target[2] - sz
    heading = math.degrees(math.atan2(dx, dz))
    pitch = -math.degrees(math.atan2(dy, math.hypot(dx, dz)))
    return Pose(seat, pitch, heading, 0.0, min(reference.fov * 1.25, 1.4))


class NotInFront(RuntimeError):
    pass


def msfs_in_front() -> None:
    """Met MSFS au premier plan et vérifie qu'aucune autre fenêtre ne couvre sa zone client.

    La capture lit l'écran : une autre fenêtre devant MSFS serait capturée à sa place.
    """
    import ctypes as ct
    from ctypes import wintypes
    from flowxav.sim.window import MSFS_TITLE_PREFIX, msfs_client_rect

    user32 = ct.windll.user32
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    found: list[int] = []

    @ct.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        buffer = ct.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buffer, 256)
        if buffer.value.startswith(MSFS_TITLE_PREFIX) and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        raise NotInFront("Fenêtre de MSFS introuvable.")
    hwnd = found[0]
    user32.keybd_event(0x12, 0, 0, 0)            # Alt : autorise SetForegroundWindow depuis ce processus
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.5)
    left, top, width, height = msfs_client_rect()
    for fx, fy in ((0.5, 0.5), (0.1, 0.15), (0.9, 0.15), (0.1, 0.9), (0.9, 0.9)):
        point = wintypes.POINT(int(left + fx * width), int(top + fy * height))
        if user32.GetAncestor(user32.WindowFromPoint(point), 2) != hwnd:
            raise NotInFront("Une autre fenêtre couvre MSFS : fermez-la ou réduisez-la, puis relancez.")


def capture(pose: Pose, out: Path) -> None:
    from imaging import grab_rgb
    from flowxav.sim.simconnect import SimConnectClient
    from flowxav.sim.window import msfs_client_rect

    msfs_in_front()
    with SimConnectClient("FlowXav profondeurs") as client:
        client.camera_acquire()
        try:
            end = time.monotonic() + 2.5                  # laisse le rendu se stabiliser
            while time.monotonic() < end:
                client.camera_set(pose)
                for _ in client.messages():
                    pass
                time.sleep(0.033)
            msfs_in_front()                                # revérifié juste avant de lire l'écran
            left, top, width, height = msfs_client_rect()
            write_png(out, width, height, grab_rgb(left, top, width, height))
        finally:
            client.camera_release()
    print(f"  capture -> {out.name}")


class Image:
    def __init__(self, path: Path) -> None:
        self.width, self.height, self.rgb = read_png(path)
        self.gray = gray(self.width, self.height, self.rgb)

    def at(self, u: float, v: float) -> float | None:
        x, y = int(u * self.width), int(v * self.height)
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.gray[y * self.width + x]
        return None


def ncc(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    num = sum(x * y for x, y in zip(da, db))
    den = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return num / den if den > 1e-6 else -1.0


def control_depth(reference: Pose, ref_img: Image, seats: list[tuple[Pose, Image]], u: float, v: float,
                  around: float = 0.5):
    """(profondeur, score) de la commande vue en (u, v), ou (None, score) si trop incertaine."""
    aspect = ref_img.width / ref_img.height
    offsets = [(dx, dy) for dy in range(-PATCH, PATCH + 1, STEP) for dx in range(-PATCH, PATCH + 1, STEP)]
    rays, values = [], []
    for dx, dy in offsets:
        pu, pv = u + dx / ref_img.width, v + dy / ref_img.height
        value = ref_img.at(pu, pv)
        if value is None:
            return None, 0.0
        rays.append(direction(reference, pu, pv, aspect))
        values.append(value)
    mean = sum(values) / len(values)
    if math.sqrt(sum((x - mean) ** 2 for x in values) / len(values)) < MIN_CONTRAST:
        return None, 0.0                                   # surface unie : rien pour comparer
    axis = direction(reference, 0.5, 0.5, aspect)
    eye = _eye(reference)
    alongs = [max(r[0] * axis[0] + r[1] * axis[1] + r[2] * axis[2], 0.2) for r in rays]
    best, best_score = None, -2.0
    depths = [round(max(0.15, around + SPAN[0]) + 0.01 * i, 2)
              for i in range(int(round((SPAN[1] - SPAN[0]) * 100)) + 1)]
    curve = []
    for depth in depths:
        points = [(eye[0] + r[0] * depth / a, eye[1] + r[1] * depth / a, eye[2] + r[2] * depth / a)
                  for r, a in zip(rays, alongs)]
        scores = []
        for pose, img in seats:
            seat_eye = _eye(pose)
            sampled = []
            for p in points:
                su, sv, visible = project(pose, (p[0] - seat_eye[0], p[1] - seat_eye[1], p[2] - seat_eye[2]),
                                          img.width / img.height)
                value = img.at(su, sv) if visible else None
                if value is None:
                    break
                sampled.append(value)
            if len(sampled) == len(values):
                scores.append(ncc(values, sampled))
        if scores:
            score = sum(scores) / len(scores)
            curve.append(score)
            if score > best_score:
                best, best_score = depth, score
    if best is None or best_score < MIN_SCORE or best in (depths[0], depths[-1]):
        return None, best_score                           # trop faible, ou au bord de la plage : pas de vrai pic
    if best_score - sorted(curve)[len(curve) // 2] < 0.15:
        return None, best_score                           # pic pas assez net : surface répétitive
    return best, best_score


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    capture_now = "--sans-capture" not in sys.argv
    aircraft, wanted = args[0], set(args[1:])
    folder = Path(__file__).parent / aircraft
    work = folder / "profondeurs"
    work.mkdir(exist_ok=True)
    profile = load_profile(aircraft)
    views = profile.carte["vues"]
    results_path = folder / "profondeurs.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    for name in views:
        if wanted and name not in wanted:
            continue
        print(f"vue {name}")
        reference = profile.view(name)
        width, height = profile.carte.get("fenetre", [1920, 1080])
        aspect = width / height
        poses = {"reference": reference}
        poses.update({seat: seat_pose(reference, eye, aspect, profile.view_depth(name)) for seat, eye in SEATS.items()})
        files = {key: work / f"{name}_{key}.png" for key in poses}
        poses_file = work / f"{name}_poses.json"
        if capture_now:
            for key, pose in poses.items():
                capture(pose, files[key])
            poses_file.write_text(json.dumps({key: pose.to_dict() for key, pose in poses.items()}, indent=1),
                                  encoding="utf-8")
        else:                                              # la pose réellement utilisée pour chaque capture
            poses = {key: Pose.from_dict(data) for key, data in json.loads(poses_file.read_text(encoding="utf-8")).items()}
        ref_img = Image(files["reference"])
        seats = [(poses[key], Image(files[key])) for key in USED_SEATS]
        canvas = Canvas(seats[0][1].width, seats[0][1].height, bytearray(seats[0][1].rgb))
        controls = [(key, c) for key, c in profile.carte["commandes"].items() if c["vue"] == name]
        kept = 0
        for key, control in controls:
            depth, score = control_depth(reference, ref_img, seats, control["u"], control["v"],
                                         profile.view_depth(name))
            used = depth if depth is not None else profile.view_depth(name)
            if depth is not None:
                results[key] = {"profondeur": depth, "score": round(score, 3)}
                kept += 1
            else:
                results.pop(key, None)
            pose, img = seats[0]
            dx, dy, dz = direction(reference, control["u"], control["v"], aspect)
            axis = direction(reference, 0.5, 0.5, aspect)
            along = max(dx * axis[0] + dy * axis[1] + dz * axis[2], 0.2)
            ex, ey, ez = _eye(reference)
            point = (ex + dx * used / along, ey + dy * used / along, ez + dz * used / along)
            sx, sy, sz = _eye(pose)
            su, sv, visible = project(pose, (point[0] - sx, point[1] - sy, point[2] - sz), img.width / img.height)
            if visible:
                canvas.ring(int(su * img.width), int(sv * img.height), 9,
                            (0, 220, 0) if depth is not None else (230, 0, 0))
        write_png(work / f"controle_{name}.png", canvas.width, canvas.height, canvas.rgb)
        print(f"  {kept}/{len(controls)} commandes à profondeur mesurée -> controle_{name}.png")
    results_path.write_text(json.dumps(dict(sorted(results.items())), indent=1, ensure_ascii=False), encoding="utf-8")
    carte_path = profile.folder / "carte.json"
    if carte_path.exists():
        carte = json.loads(carte_path.read_bytes().decode("utf-8"))
        for key, control in carte["commandes"].items():
            control.pop("profondeur", None)
            if key in results:
                control["profondeur"] = results[key]["profondeur"]
        carte_path.write_bytes(json.dumps(carte, indent=1, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8"))
        print(f"carte mise à jour : {carte_path}")


if __name__ == "__main__":
    main()
