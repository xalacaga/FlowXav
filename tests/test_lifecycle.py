"""Cycle de vie des services et fils, sans ouvrir MSFS ni une vraie fenêtre."""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from flowxav.guide import Guide
from flowxav.overlay import Overlay
from flowxav.sim.camera import CameraDirector
from flowxav.sim.simconnect import Pose, SimConnectError
from flowxav.simulator import FlightPhaseTracker, Simulator, flight_phase
from tests.test_guide import FakeSimulator, Recorder


def eventually(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


class FakeCameraClient:
    """Seule la lecture existe : toute tentative de prendre la caméra ferait échouer le test."""

    def __init__(self):
        self.closed = False
        self.reads = 0
        self.pose = Pose((-0.5, 1.5, 15.0), 10, 20, 0, 0.9)

    def camera_get(self, timeout_s=3.0):
        self.reads += 1
        return self.pose

    def close(self):
        self.closed = True


def test_camera_only_follows_the_view_and_never_takes_it():
    client = FakeCameraClient()
    camera = CameraDirector(client_factory=lambda: client)
    assert camera.current_pose() is None
    camera.show(Pose((0, 0, 0), 0, 0, 0, 0.8))
    eventually(lambda: camera.current_pose() == client.pose)
    camera.release()
    assert camera.current_pose() is None
    reads = client.reads
    time.sleep(0.15)
    assert client.reads <= reads + 1                            # plus de lecture une fois libéré
    camera.close()
    assert client.closed
    assert camera._thread is not None and not camera._thread.is_alive()


class HeadlessOverlay(Overlay):
    """Même file de commandes que l'overlay Tk, sans interface graphique."""

    def _run(self):
        self._ready.set()
        while True:
            command, _payload = self._commands.get(timeout=1)
            if command == "quit":
                return


def test_overlay_thread_stops_cleanly():
    overlay = HeadlessOverlay()
    overlay.show(0.5, 0.5, "BAT")
    assert overlay._thread is not None and overlay._thread.is_alive()
    overlay.close()
    assert not overlay._thread.is_alive()


def test_guide_thread_is_joined_on_close():
    guide = Guide(FakeSimulator(), camera=Recorder(), overlay=Recorder())
    guide.start_thread()
    guide.close()
    assert not guide._thread.is_alive()


def test_simulator_reconnects_after_a_lost_client(monkeypatch):
    import flowxav.simulator as simulator_module

    monkeypatch.setattr(simulator_module, "RETRY_S", 0.01)
    monkeypatch.setattr(simulator_module, "STATE_PERIOD_S", 0.01)
    monkeypatch.setattr(simulator_module, "TITLE_PERIOD_S", 0.01)

    class Source:
        def read(self, client):
            return client.read_state()

        def help(self):
            return ""

    profile = SimpleNamespace(id="test", source=Source(), data={})
    monkeypatch.setattr(simulator_module, "detect", lambda _title: profile)

    class Client:
        def __init__(self, generation):
            self.generation, self.closed = generation, False

        def read_title(self):
            return "Test aircraft"

        def read_state(self):
            if self.generation == 1:
                raise SimConnectError("connexion fermée")
            return {"generation": self.generation}

        def close(self):
            self.closed = True

    clients = []

    def factory():
        client = Client(len(clients) + 1)
        clients.append(client)
        return client

    simulator = Simulator(client_factory=factory)
    simulator.start()
    eventually(lambda: simulator.snapshot().values.get("generation") == 2)
    simulator.close()
    assert len(clients) >= 2 and clients[0].closed and clients[-1].closed


def test_pywebview_server_starts_and_stops(monkeypatch, tmp_path):
    import flowxav.desktop as desktop

    class Server:
        should_exit = False
        force_exit = False
        ran = False

        def run(self):
            self.ran = True
            while not self.should_exit and not self.force_exit:
                time.sleep(0.005)

    calls = {}

    class Hook:
        def __init__(self):
            self.callback = None

        def __iadd__(self, callback):
            self.callback = callback
            return self

    class Events:
        closed = Hook()

    window = SimpleNamespace(events=Events())

    def create_window(*args, **kwargs):
        calls["window"] = (args, kwargs)
        return window

    def start(**kwargs):
        calls["start"] = kwargs

    fake_webview = SimpleNamespace(create_window=create_window, start=start)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(desktop, "_webview2_version", lambda: "test")
    monkeypatch.setattr(desktop, "_wait_until_ready", lambda _port: None)
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path))
    server = Server()
    desktop._run_window("http://127.0.0.1:8790", 8790, server)
    assert server.ran and server.should_exit and not server.force_exit
    assert calls["start"]["gui"] == "edgechromium"
    assert calls["start"]["storage_path"] == str(tmp_path / "webview")


@pytest.mark.parametrize(("values", "expected"), [
    ({}, ""),
    ({"SIM ON GROUND": 1, "GROUND VELOCITY": 0}, "parking"),
    ({"SIM ON GROUND": 1, "GROUND VELOCITY": 12}, "roulage"),
    ({"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 800, "VERTICAL SPEED": 1200}, "montee_initiale"),
    ({"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 25000, "VERTICAL SPEED": 0}, "croisiere"),
    ({"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 8000, "VERTICAL SPEED": -1200}, "descente"),
    ({"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 1200, "VERTICAL SPEED": -500}, "approche"),
    ({"SIM ON GROUND": 1, "FLOWXAV_AIRBORNE_SEEN": 1, "GROUND VELOCITY": 20}, "apres_atterrissage"),
    ({"SIM ON GROUND": 1, "FLOWXAV_AIRBORNE_SEEN": 1, "GROUND VELOCITY": 0}, "fin_vol"),
])
def test_flight_phase(values, expected):
    assert flight_phase(values) == expected


def test_flight_phase_waits_before_changing_and_uses_fmc_cruise_altitude():
    tracker = FlightPhaseTracker(hold_s=5)
    climb = {"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 5000, "INDICATED ALTITUDE": 12000,
             "FMC_CruiseAlt": 35000, "VERTICAL SPEED": 1200}
    assert tracker.update(climb, 0) == "montee"
    level_off = dict(climb, **{"VERTICAL SPEED": 0})
    assert tracker.update(level_off, 10) == "montee"       # palier intermédiaire : toujours montée
    cruise = dict(climb, **{"INDICATED ALTITUDE": 34900, "VERTICAL SPEED": 0})
    assert tracker.update(cruise, 20) == "montee"
    assert tracker.update(cruise, 26) == "croisiere"       # changement stable pendant cinq secondes
