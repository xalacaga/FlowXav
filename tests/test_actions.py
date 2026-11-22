"""« Fais-le pour moi » : ce que FlowXav envoie pour chaque genre d'étape, sans simulateur."""

import time

import pytest

from flowxav import actions, settings
from flowxav.aircraft import load_profile
from flowxav.actions import MOUSE_LEFT_RELEASE, MOUSE_LEFT_SINGLE, NotActionable, plan_step

PROFILE = load_profile("pmdg_737_900")


def step_of(procedure_id, step_id):
    return next(s for s in PROFILE.procedure(procedure_id)["etapes"] if s["id"] == step_id)


def events(plan):
    return [(s.event, s.parameter) for s in plan.sends]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    from flowxav import licence
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(licence, "accepted", lambda _profile: True)     # contrat accepté (voir test_licence)


def test_guarded_switch_lifts_guard_sets_position_and_closes_it():
    plan = plan_step(PROFILE, step_of("mise_sous_tension_apu", "bat"), {}, {"ELEC_BatSelector": 0})
    assert events(plan) == [("EVT_OH_ELEC_BATTERY_GUARD", 1), ("EVT_OH_ELEC_BATTERY_SWITCH", 1), ("EVT_OH_ELEC_BATTERY_GUARD", 0)]
    assert plan.verifies


def test_spring_loaded_apu_start_uses_start_position():
    plan = plan_step(PROFILE, step_of("mise_sous_tension_apu", "apu_start"), {}, {})
    assert events(plan) == [("EVT_OH_LIGHTS_APU_START", 2)]


def test_flight_value_is_sent_with_a_set_event():
    plan = plan_step(PROFILE, step_of("demarrage_roulage", "mcp_speed"), {"v2": 162}, {})
    assert events(plan) == [("EVT_MCP_IAS_SET", 162)]
    with pytest.raises(NotActionable) as error:
        plan_step(PROFILE, step_of("demarrage_roulage", "mcp_speed"), {}, {})
    assert error.value.reason == "valeur_inconnue"


def test_cdu_step_types_the_flight_value_then_presses_the_key():
    plan = plan_step(PROFILE, step_of("programmation_fmc", "origin"), {"origine": "LFBO"}, {})
    pressed = [e for e, p in events(plan) if p == MOUSE_LEFT_SINGLE]
    assert pressed == ["EVT_CDU_L_CLR", "EVT_CDU_L_L", "EVT_CDU_L_F", "EVT_CDU_L_B", "EVT_CDU_L_O", "EVT_CDU_L_L1"]
    assert not plan.verifies
    reserves = plan_step(PROFILE, step_of("programmation_fmc", "reserves"), {"reserve": "1.1"}, {})
    assert [e for e, p in events(reserves) if p == MOUSE_LEFT_SINGLE][1:4] == ["EVT_CDU_L_1", "EVT_CDU_L_DOT", "EVT_CDU_L_1"]


def test_pilot_controls_and_waits_are_never_automated():
    for procedure_id, step_id in [("demarrage_roulage", "eng2_idle"),      # levier carburant
                                  ("demarrage_roulage", "eng2_cutout"),    # relâchement du démarreur
                                  ("demarrage_roulage", "flaps"),          # levier des volets
                                  ("mise_sous_tension_apu", "apu_gen_1")]:  # interrupteur à ressort
        with pytest.raises(NotActionable):
            plan_step(PROFILE, step_of(procedure_id, step_id), {}, {})


def test_remembered_inversion_flips_a_boolean_switch():
    step = step_of("preparation_poste", "fuel_aft_1")
    assert events(plan_step(PROFILE, step, {}, {"FUEL_PumpAftSw[0]": False})) == [("EVT_OH_FUEL_PUMP_1_AFT", 1)]
    actions.remember_inversion(PROFILE, "FUEL_PumpAftSw[0]")
    assert events(plan_step(PROFILE, step, {}, {"FUEL_PumpAftSw[0]": False})) == [("EVT_OH_FUEL_PUMP_1_AFT", 0)]
    assert settings.section("actions_inversees")["pmdg_737_900"] == ["FUEL_PumpAftSw[0]"]


class FakeClient:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transmit_event(self, event, data):
        self.sent.append((event, data))


def test_do_step_on_a_cdu_key_sends_and_validates(monkeypatch):
    from tests.test_guide import make_guide
    monkeypatch.setattr(actions, "PAUSE_S", 0)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    guide, _simulator, _camera, _overlay = make_guide()
    client = FakeClient()
    guide.action_client_factory = lambda: client
    guide.start(PROFILE, "programmation_fmc")
    guide._session.index = 0                       # MENU
    guide._tick()
    assert guide.status()["etape"]["faisable"]
    assert guide.do_step()["ok"]
    deadline = time.monotonic() + 2
    while guide.status()["etape"]["action"] != "fait" and time.monotonic() < deadline:
        pass
    assert guide.status()["etape"]["validee"] and client.sent[0][1] == MOUSE_LEFT_SINGLE
    assert client.sent[1][1] == MOUSE_LEFT_RELEASE


def test_auto_mode_performs_doable_steps_without_taking_the_camera(monkeypatch):
    from tests.test_guide import make_guide
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    guide, _simulator, camera, _overlay = make_guide()
    client = FakeClient()
    guide.action_client_factory = lambda: client
    guide.set_auto(True)
    guide.start(PROFILE, "programmation_fmc")
    guide._session.index = 0                       # MENU : faisable
    guide._tick()
    assert not any(call[0] == "show" for call in camera.calls)
    deadline = time.monotonic() + 2
    while not client.sent and time.monotonic() < deadline:
        pass
    assert client.sent and guide.status()["auto"]
