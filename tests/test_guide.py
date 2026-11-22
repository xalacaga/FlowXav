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
    assert not guide.status()["etape"]["validee"]          # étape vérifiable : « Fait » ne la valide pas
    guide.start(load_profile("pmdg_737_900"), "mise_sous_tension_apu")
    guide._tick()
    assert guide.status()["etape"]["manuelle"]
    guide.confirm()
    assert guide.status()["etape"]["validee"]              # étape manuelle : « Fait » la valide
    guide.start(load_profile("pmdg_737_900"), "demo_mise_sous_tension")
    guide.go(+3)
    assert guide.status()["index"] == 3 and not guide.status()["etape"]["validee"]
    guide.go(-10)
    assert guide.status()["index"] == 0
    guide.stop()
    assert not guide.status()["actif"]
    assert camera.calls[-1][0] == "release" and overlay.calls[-1][0] == "hide"


def test_evt_step_is_manual_unless_it_names_a_field_to_check():
    guide, *_ = make_guide()
    profile = load_profile("pmdg_737_900")
    assert guide._manual(profile, {"commande": "EVT_CDU_L_EXEC", "attendu": {"egal": 1}})
    assert not guide._manual(profile, {"commande": "EVT_CDU_L_R1", "verifier": "FMC_V1", "attendu": {"min": 80}})
    assert guide._manual(profile, {"commande": "EVT_CDU_L_R1", "verifier": "FMC_V1"})
    assert guide._manual(profile, {"commande": "APU_Selector", "attendu": {"egal": 1}, "confirmation": True})
    assert not guide._manual(profile, {"commande": "APU_Selector", "attendu": {"egal": 1}})


def _proc(*steps):
    return {"id": "p", "etapes": [dict(id=f"s{i}", commande=c, **extra) for i, (c, extra) in enumerate(steps)]}


def test_resume_after_longest_satisfied_prefix():
    from flowxav.guide import resume_index
    procedure = _proc(("A", {"attendu": {"egal": 1}}), ("EVT_X", {}), ("B", {"attendu": {"egal": 1}}),
                      ("C", {"attendu": {"egal": 1}}), ("D", {"attendu": {"egal": 1}}))
    assert resume_index(procedure, {}) == 0
    assert resume_index(procedure, {"A": 1}) == 1                    # la manuelle qui suit reste à faire
    assert resume_index(procedure, {"A": 1, "B": 1}) == 3            # la manuelle avant B est réputée faite
    assert resume_index(procedure, {"A": 1, "B": 1, "D": 1}) == 3    # C manque : on s'arrête à C


def test_suggests_procedure_after_last_completed_one():
    from flowxav.guide import suggest
    demo = dict(_proc(("A", {"attendu": {"egal": 1}})), id="demo", demonstration=True)
    first = dict(_proc(("A", {"attendu": {"egal": 1}})), id="un")
    second = dict(_proc(("B", {"attendu": {"egal": 1}})), id="deux")
    third = dict(_proc(("C", {"attendu": {"egal": 1}})), id="trois")
    procedures = [demo, first, second, third]
    assert suggest(procedures, {"A": 0}) == "un"
    assert suggest(procedures, {"A": 1}) == "deux"
    assert suggest(procedures, {"A": 0, "B": 1}) == "trois"         # la dernière accomplie compte
    assert suggest(procedures, {"C": 1}) == "trois"
    assert suggest(procedures, {}) is None


def test_steps_already_done_are_skipped_but_not_after_previous(monkeypatch):
    guide, simulator, _camera, _overlay = make_guide()
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    simulator.values.update({"ELEC_BatSelector": 1, "ELEC_StandbyPowerSelector": 2})
    guide.start(load_profile("pmdg_737_900"), "demo_mise_sous_tension")
    guide._tick()
    assert guide.status()["etape"]["id"] == "emer_exit"            # BAT et STANDBY déjà faits
    simulator.values["LTS_EmerExitSelector"] = 1
    simulator.values["COMM_FastenBeltsSelector"] = 2
    guide._tick()
    clock[0] += HOLD_S + 0.01
    guide._tick()
    clock[0] += PAUSE_DONE_S + 0.01
    guide._tick()
    guide._tick()
    assert guide.status()["etape"]["id"] == "position"             # FASTEN BELTS déjà fait : passé
    guide.go(-1)
    guide._tick()
    assert guide.status()["etape"]["id"] == "fasten_belts"         # Précédente : on y reste


def test_variants_of_a_phase_count_as_one_step_of_the_journey():
    from flowxav.guide import journey, suggest
    apu = dict(_proc(("APU", {"attendu": {"egal": 1}})), id="apu", phase=1)
    gpu = dict(_proc(("GPU", {"attendu": {"egal": 1}})), id="gpu", phase=1)
    prep = dict(_proc(("P", {"attendu": {"egal": 1}})), id="prep", phase=2)
    procedures = [apu, gpu, prep]
    assert suggest(procedures, {"APU": 0}) == "apu"                 # rien de fait : première variante
    assert suggest(procedures, {"GPU": 1}) == "prep"                # variante GPU faite : phase suivante
    assert journey(procedures, {"GPU": 1}) == {"apu": "fait", "gpu": "fait", "prep": "propose"}


def test_real_profile_has_two_power_up_variants():
    profile = load_profile("pmdg_737_900")
    phase_one = [p["id"] for p in profile.procedures() if p.get("phase") == 1]
    assert phase_one == ["mise_sous_tension_apu", "mise_sous_tension_gpu"]


def test_flight_phase_suggests_full_flight_and_both_approach_variants():
    from flowxav.guide import suggest

    procedures = load_profile("pmdg_737_900").procedures()
    climb = {"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 5000, "VERTICAL SPEED": 1200}
    descent = {"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 8000, "VERTICAL SPEED": -1200}
    approach = {"SIM ON GROUND": 0, "PLANE ALT ABOVE GROUND": 1500, "VERTICAL SPEED": -500}
    assert suggest(procedures, climb) == "montee"
    assert suggest(procedures, descent) == "descente"
    assert suggest(procedures, approach) in {"approche_manuelle", "approche_autoland"}
    assert {p["id"] for p in procedures if p.get("phase") == 8} == {
        "approche_manuelle", "approche_autoland",
    }
    after_landing = {
        "SIM ON GROUND": 1, "FLOWXAV_AIRBORNE_SEEN": 1, "GROUND VELOCITY": 10,
        "GENERAL ENG COMBUSTION:1": 1,
    }
    assert suggest(procedures, after_landing) == "apres_atterrissage"
    after_landing.update({
        "SPOILERS HANDLE POSITION": 0, "FLAPS HANDLE INDEX": 0, "MAIN_AutobrakeSelector": 1,
        "LTS_LandingLtFixedSw[0]": False, "LTS_LandingLtFixedSw[1]": False, "LTS_TaxiSw": True,
        "APU_EGTNeedle": 200, "BRAKE PARKING POSITION": 1,
    })
    assert suggest(procedures, after_landing) == "fin_vol"


def test_brief_camera_gives_the_view_back_during_approach(monkeypatch):
    """Phases où l'on pilote (« camera »: « breve ») : la vue est rendue au bout de BRIEF_S."""
    from flowxav.guide import BRIEF_S
    guide, _simulator, camera, overlay = make_guide()
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    profile = load_profile("pmdg_737_900")
    procedure = next(p for p in profile.procedures() if p.get("camera") == "breve")
    guide.start(profile, procedure["id"])
    guide._session.index = next(i for i, s in enumerate(procedure["etapes"]) if s.get("commande") and not s.get("sans_cible"))
    guide._tick()
    assert ("release", ()) not in camera.calls
    clock[0] += BRIEF_S + 0.1
    guide._tick()
    assert camera.calls[-1][0] == "release" and overlay.calls[-1][0] == "hide"


def test_landing_procedures_use_brief_camera():
    profile = load_profile("pmdg_737_900")
    brief = {p["id"] for p in profile.procedures() if p.get("camera") == "breve"}
    assert brief == {"approche_manuelle", "approche_autoland", "apres_atterrissage"}


def test_flight_phase_memory_is_reset_for_a_new_flight():
    """Après un vol, recharger l'avion ou un état de départ doit repartir de « parking », pas de « fin de vol »."""
    from flowxav.simulator import Simulator, _raw_flight_phase
    simulator = Simulator(client_factory=lambda: None)
    simulator._airborne_seen = simulator._descent_seen = True
    parked = {"SIM ON GROUND": 1.0, "GROUND VELOCITY": 0.0, "FLOWXAV_AIRBORNE_SEEN": simulator._airborne_seen}
    assert _raw_flight_phase(parked) == "fin_vol"
    simulator.reset_flight()
    parked["FLOWXAV_AIRBORNE_SEEN"] = simulator._airborne_seen
    assert _raw_flight_phase(parked) == "parking" and not simulator._descent_seen
