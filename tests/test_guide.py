"""Moteur de guidage : conditions, enchaînement des étapes, validation manuelle — sans simulateur."""

import time

from flowxav.aircraft import load_profile
from flowxav.guide import HOLD_S, PAUSE_DONE_S, Guide, satisfied
from flowxav.simulator import Snapshot


class FakeSimulator:
    def __init__(self):
        self.values = {}

    def snapshot(self):
        return Snapshot(connected=True, profile_id="pmdg_737_900", source_ok=True, values=dict(self.values))


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args))


def make_guide():
    simulator = FakeSimulator()
    camera, overlay = Recorder(), Recorder()
    return Guide(simulator, camera=camera, overlay=overlay), simulator, camera, overlay


def test_conditions():
    assert satisfied({"egal": 1}, 1) and not satisfied({"egal": 1}, 0)
    assert satisfied({"parmi": [1, 2]}, 2) and not satisfied({"parmi": [1, 2]}, 0)
    assert satisfied({"different": 0}, 3) and not satisfied({"different": 0}, 0)
    assert satisfied({"min": 10, "max": 20}, 15) and not satisfied({"min": 10}, 5)
    assert not satisfied({"egal": 1}, None)


def test_step_validates_after_hold_then_advances(monkeypatch):
    guide, simulator, camera, overlay = make_guide()
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    guide.start(load_profile("pmdg_737_900"), "demo_mise_sous_tension")
    guide._tick()
    assert camera.calls[0][0] == "show" and overlay.calls[0][0] == "show"
    assert guide.status()["etape"]["id"] == "bat"

    simulator.values["ELEC_BatSelector"] = 1
    guide._tick()
    clock[0] += HOLD_S + 0.01
    guide._tick()
    assert guide.status()["etape"]["validee"]
    assert ("mark_done", ()) in overlay.calls
    clock[0] += PAUSE_DONE_S + 0.01
    guide._tick()
    assert guide.status()["etape"]["id"] == "standby_power"


def test_brief_flicker_does_not_validate(monkeypatch):
    guide, simulator, _camera, _overlay = make_guide()
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    guide.start(load_profile("pmdg_737_900"), "demo_mise_sous_tension")
    guide._tick()
    simulator.values["ELEC_BatSelector"] = 1
    guide._tick()
    simulator.values["ELEC_BatSelector"] = 0
    clock[0] += HOLD_S + 0.5
    guide._tick()
    assert not guide.status()["etape"]["validee"]


def test_manual_confirmation_and_navigation(monkeypatch):
    guide, _simulator, camera, overlay = make_guide()
    guide.start(load_profile("pmdg_737_900"), "demo_mise_sous_tension")
    guide._tick()
    guide.confirm()
    assert guide.status()["etape"]["validee"]
    guide.go(+3)
    assert guide.status()["index"] == 3 and not guide.status()["etape"]["validee"]
    guide.go(-10)
    assert guide.status()["index"] == 0
    guide.stop()
    assert not guide.status()["actif"]
    assert camera.calls[-1][0] == "release" and overlay.calls[-1][0] == "hide"
