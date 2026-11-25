"""Tests opt-in des frontières réelles : MSFS, SimBrief et exécutable distribué.

Ils sont ignorés par défaut afin de ne jamais déplacer la caméra, redémarrer le
simulateur ou contacter SimBrief à l'insu de l'utilisateur.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from flowxav.aircraft import detect
from flowxav.sim.simconnect import SimConnectClient


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "oui"}


def wait_until(predicate, timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    pytest.fail(message)


@pytest.mark.live_msfs
@pytest.mark.skipif(not enabled("FLOWXAV_RUN_LIVE_MSFS"), reason="activer FLOWXAV_RUN_LIVE_MSFS=1")
def test_live_msfs_reads_aircraft_and_camera():
    """Connexion non destructive : lit le titre et la pose, sans tenir la caméra."""
    with SimConnectClient("FlowXav pytest lecture") as client:
        title = client.read_title()
        pose = client.camera_get()
    assert title.strip()
    assert 0.05 <= pose.fov <= 3.2
    profile = detect(title)
    if enabled("FLOWXAV_REQUIRE_KNOWN_AIRCRAFT"):
        assert profile is not None, f"avion non reconnu : {title}"
    if profile is not None:
        from flowxav.simulator import flight_phase

        with SimConnectClient("FlowXav pytest phase") as client:
            variables = [tuple(item) for item in profile.data.get("suivi_vol", {}).get("simvars", [])]
            values = client.read_simvars(variables)
        assert flight_phase(values), "phase de vol non détectée"


@pytest.mark.live_msfs
@pytest.mark.skipif(not enabled("FLOWXAV_TEST_RECONNECT"), reason="test manuel : activer FLOWXAV_TEST_RECONNECT=1")
def test_live_msfs_disconnect_then_reconnect():
    """L'utilisateur ferme puis relance MSFS pendant la fenêtre du test."""
    from flowxav.simulator import Simulator

    timeout = float(os.getenv("FLOWXAV_RECONNECT_TIMEOUT", "180"))
    simulator = Simulator()
    simulator.start()
    try:
        wait_until(lambda: simulator.snapshot().connected, 30, "connexion initiale à MSFS absente")
        print("\nFermez MSFS, puis relancez-le et chargez un vol.")
        wait_until(lambda: not simulator.snapshot().connected, timeout, "déconnexion MSFS non observée")
        wait_until(lambda: simulator.snapshot().connected, timeout, "reconnexion MSFS non observée")
        assert simulator.snapshot().title
    finally:
        simulator.close()


@pytest.mark.live_msfs
@pytest.mark.skipif(not enabled("FLOWXAV_TEST_CAMERA_OVERLAY"),
                    reason="test visuel : activer FLOWXAV_TEST_CAMERA_OVERLAY=1")
def test_live_camera_overlay_lifecycle():
    """Suit la vue actuelle et affiche un anneau central deux secondes, puis libère tout."""
    from flowxav.overlay import Overlay
    from flowxav.sim.camera import CameraDirector

    with SimConnectClient("FlowXav pytest pose") as client:
        pose = client.camera_get()
    camera, overlay = CameraDirector(), Overlay()
    try:
        camera.show(pose)
        overlay.show(0.5, 0.5, "TEST FLOWXAV", pose, camera.current_pose)
        time.sleep(2)
    finally:
        overlay.close()
        camera.close()
    assert camera._thread is not None and not camera._thread.is_alive()
    assert overlay._thread is not None and not overlay._thread.is_alive()


@pytest.mark.live_network
@pytest.mark.skipif(not enabled("FLOWXAV_RUN_LIVE_SIMBRIEF"), reason="activer FLOWXAV_RUN_LIVE_SIMBRIEF=1")
def test_live_simbrief_import_is_confined_to_tmp(tmp_path, monkeypatch):
    """Télécharge un vrai OFP et sa route, uniquement dans le dossier temporaire Pytest."""
    from flowxav import simbrief

    identifier = os.getenv("FLOWXAV_SIMBRIEF_ID", "").strip()
    if not identifier:
        pytest.skip("FLOWXAV_SIMBRIEF_ID est requis")
    routes = tmp_path / "routes"
    routes.mkdir()
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path / "settings"))
    result = simbrief.import_latest(identifier, {"dossiers": [str(routes)], "format": "pmdg_rte"})
    target = routes / result["fichier"]
    assert result["origine"] and result["destination"]
    assert target.is_file() and target.stat().st_size > 0
    assert target.parent == routes


@pytest.mark.installed
def test_installed_executable_smoke_cycle():
    """Le binaire construit/ installé doit démarrer son API puis s'arrêter proprement."""
    value = os.getenv("FLOWXAV_INSTALLED_EXE", "").strip()
    if not value:
        pytest.skip("FLOWXAV_INSTALLED_EXE doit pointer vers FlowXav.exe")
    executable = Path(value)
    assert executable.is_file(), executable
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    completed = subprocess.run(
        [str(executable), "--smoke-test", "--port", str(port)],
        timeout=30,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
