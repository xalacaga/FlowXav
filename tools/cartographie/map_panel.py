"""Cartographie par clics, vue verrouillée : chaque clic est relié au champ d'état qui change juste après.

    python tools/cartographie/map_panel.py <avion> <nom_de_vue> <durée_s>
    lit <avion>/pose_<nom>.json, écrit <avion>/seances/<nom>.log

Pour les interrupteurs et sélecteurs qui ont un état publié (SDK PMDG…).
La vue est verrouillée par FlowXav pendant toute la séance : sans verrou, elle
dérive et les points se décalent (constaté : 7 à 10 % en quatre minutes). Les
clics passent quand même. Les boutons-poussoirs sans état se repèrent à l'image
(vision.py).
"""

import ctypes as ct
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flowxav.aircraft import load_profile  # noqa: E402
from flowxav.sim.camera import CameraDirector  # noqa: E402
from flowxav.sim.simconnect import Pose, SimConnectClient  # noqa: E402
from flowxav.sim.window import msfs_client_rect  # noqa: E402

aircraft, view, duration = sys.argv[1], sys.argv[2], float(sys.argv[3])
folder = Path(__file__).parent / aircraft
pose = Pose.from_dict(json.loads((folder / f"pose_{view}.json").read_text(encoding="utf-8"))["pose"])
profile = load_profile(aircraft)
LOG = folder / "seances" / f"{view}.log"
LOG.parent.mkdir(exist_ok=True)
T0 = time.monotonic()
# Les voyants, barres électriques et jauges changent par conséquence : jamais un clic.
IGNORED = ("annun", "BusPowered", "Needle", "Display", "FUEL_Qty", "FMC_", "AircraftModel", "IRS_aligned",
           "MCP_indication", "AFS_", "ENG_StartValve")


def log(message: str) -> None:
    line = f"[{time.monotonic() - T0:6.1f}s] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def controls(values: dict) -> dict:
    return {k: v for k, v in values.items()
            if not isinstance(v, str) and not any(token in k for token in IGNORED)
            and (not isinstance(v, float) or k.startswith("MCP_"))}


# ------------------------------------------------------------------ crochet souris
user32, kernel32 = ct.windll.user32, ct.windll.kernel32
HOOKPROC = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ct.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ct.c_ssize_t
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class MSLLHOOKSTRUCT(ct.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ct.c_size_t)]


BUTTONS = {0x0201: "gauche", 0x020A: "molette"}
clicks: list[dict] = []
clicks_lock = threading.Lock()


def hook() -> None:
    @HOOKPROC
    def proc(code, wparam, lparam):
        if code == 0 and wparam in BUTTONS:
            info = ct.cast(lparam, ct.POINTER(MSLLHOOKSTRUCT)).contents
            area = msfs_client_rect()
            if area:
                u, v = (info.pt.x - area[0]) / area[2], (info.pt.y - area[1]) / area[3]
                if 0 <= u <= 1 and 0 <= v <= 1:
                    with clicks_lock:
                        clicks.append({"t": time.monotonic(), "u": u, "v": v, "bouton": BUTTONS[wparam]})
        return user32.CallNextHookEx(None, code, wparam, lparam)

    user32.SetWindowsHookExW(14, proc, kernel32.GetModuleHandleW(None), 0)
    msg = wintypes.MSG()
    while user32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ct.byref(msg))
        user32.DispatchMessageW(ct.byref(msg))


def main() -> None:
    camera = CameraDirector()
    camera.show(pose)
    threading.Thread(target=hook, daemon=True).start()
    client = SimConnectClient("FlowXav cartographie")
    log(f"séance {view} : vue {pose.to_dict()}, fenêtre {msfs_client_rect()}")
    previous = controls(profile.source.read(client))
    log(f"{len(previous)} commandes surveillées — cliquez les commandes du panneau")
    try:
        while time.monotonic() - T0 < duration:
            time.sleep(0.08)
            now = time.monotonic()
            current = controls(profile.source.read(client))
            for key in (k for k, value in current.items() if value != previous.get(k)):
                with clicks_lock:
                    recent = [c for c in clicks if now - 1.5 <= c["t"] <= now]
                if recent:
                    c = recent[-1]
                    log(f"{key}: {previous.get(key)} -> {current[key]}  clic {c['bouton']} à ({c['u']:.3f}, {c['v']:.3f})")
                else:
                    log(f"{key}: {previous.get(key)} -> {current[key]}  (aucun clic récent : conséquence)")
            previous = current
    finally:
        camera.close()
        client.close()
        log("fin de séance, caméra rendue")


if __name__ == "__main__":
    main()
