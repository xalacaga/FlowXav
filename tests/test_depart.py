"""État de départ : pilotage du CDU (simulé à partir des pages réelles) et vérification."""

import pytest

from flowxav import depart
from flowxav.aircraft import load_profile
from flowxav.state.pmdg_cdu import COLUMNS, ROWS, SCREEN_SIZE, THIRD_PARTY_EVENT_ID_MIN, find_key

# Page MENU réelle : « PMDG » en en-tête (ligne 7), « SETUP> » en face de R4 (ligne 8).
MENU = ["MENU", "", "<FMC      <REQ>", "", "<DLNK", "", "<DFDAU", "PMDG".rjust(23), "SETUP>".rjust(24), "", "FS ACTIONS>".rjust(24), "", "", ""]
SETUP = ["PMDG SETUP", "PANEL".rjust(24), "<AIRCRAFT    STATE SAVE>", "PANEL".rjust(24), "<OPTIONS     STATE LOAD>",
         "", "", "", "", "", "", " RETURN TO", "<MENU", ""]
LOAD_1 = ["LOAD PANEL STATE1/3", "", "<approach", "", "<Apron", "", "<APRONLONG", "", "<climb", "",
          "<COLDDARKGPU", "-" * 24, "<RETURN", ""]
LOAD_2 = ["LOAD PANEL STATE2/3", "", "<COLDDARK", "", "<cruise", "", "", "", "", "", "", "-" * 24, "<RETURN", ""]
# Après le choix de l'état : voyant EXEC allumé et invite <CANCEL (page réelle).
PENDING = ["LOAD PANEL STATE2/3", "", "<COLDDARK", "", "<cruise", "", "", "", "", "", "", "-" * 24, "<CANCEL", ""]
LOADED = ["MENU", "", "<FMC", "", "", "", "", "", "", "", "", "", "", "PANEL STATE LOADED"]


def raw(lines, powered=True):
    data = bytearray(SCREEN_SIZE)
    for row in range(ROWS):
        text = lines[row].ljust(COLUMNS)[:COLUMNS]
        for column, char in enumerate(text):
            data[(column * ROWS + row) * 3] = ord(char)
    data[-1] = 1 if powered else 0
    return bytes(data)


OFFSETS = {"MENU": 551, "NEXT_PAGE": 560, "R4": 543, "R2": 541, "L1": 534, "L5": 538, "EXEC": 556}
KEYS = {THIRD_PARTY_EVENT_ID_MIN + n: name for name, n in OFFSETS.items()}
EVENTS = {f"EVT_CDU_L_{name}": THIRD_PARTY_EVENT_ID_MIN + n for name, n in OFFSETS.items()}   # table tirée du SDK


class FakeCdu:
    """Client SimConnect simulé : un automate de pages piloté par les touches."""

    def __init__(self, powered=True):
        self.page, self.powered, self.pressed = SETUP, powered, []

    def read_client_data(self, _name, _size, timeout_s=2.0):
        return raw(self.page, self.powered)

    def transmit_event(self, event, data):
        if data != 0x20000000:
            return
        key = KEYS.get(int(event[1:]), "?")
        self.pressed.append(key)
        moves = {("MENU", None): MENU, ("R4", id(MENU)): SETUP, ("R2", id(SETUP)): LOAD_1,
                 ("NEXT_PAGE", id(LOAD_1)): LOAD_2, ("L1", id(LOAD_2)): PENDING, ("EXEC", id(PENDING)): LOADED}
        self.page = moves.get((key, None if key == "MENU" else id(self.page)), self.page)


def test_find_key_reads_sides_and_lines():
    assert find_key(SETUP, "STATE LOAD") == "R2"
    assert find_key(LOAD_1, "COLDDARK", whole_word=True) is None     # COLDDARKGPU n'est pas COLDDARK
    assert find_key(LOAD_2, "colddark", whole_word=True) == "L1"
    assert find_key(MENU, "PMDG SETUP") == "R4"


def test_load_panel_state_walks_the_cdu_pages():
    from flowxav.state.pmdg_cdu import PmdgCdu
    client = FakeCdu()
    lines = depart.load_panel_state(PmdgCdu(client, events=EVENTS), "COLDDARK")
    assert client.pressed == ["MENU", "R4", "R2", "NEXT_PAGE", "L1", "EXEC"]
    assert "PANEL STATE LOADED" in lines[13]


def test_unpowered_cdu_is_reported():
    from flowxav.state.pmdg_cdu import PmdgCdu
    with pytest.raises(depart.DepartError) as error:
        depart.load_panel_state(PmdgCdu(FakeCdu(powered=False), events=EVENTS), "COLDDARK")
    assert error.value.code == "cdu_eteint"


def test_start_state_checks_flag_throttle_and_brake():
    config = load_profile("pmdg_737_900").data["depart"]
    pmdg = {"MAIN_GearLever": 2, "ELEC_BatSelector": 0, "APU_Selector": 0,
            "IRS_ModeSelector[0]": 0, "IRS_ModeSelector[1]": 0, "ENG_StartSelector[0]": 1, "ENG_StartSelector[1]": 1}
    simvars = {"GENERAL ENG THROTTLE LEVER POSITION:1": 99.0, "GENERAL ENG THROTTLE LEVER POSITION:2": 0.0,
               "BRAKE PARKING POSITION": 0.0, "FLAPS HANDLE INDEX": 0.0, "SPOILERS HANDLE POSITION": 0.0}
    results = {r["id"]: r["ok"] for r in depart.check(config, pmdg, simvars)}
    assert not results["manette_1"] and results["manette_2"] and not results["frein_parc"]
    assert all(ok for key, ok in results.items() if key not in ("manette_1", "frein_parc"))


def test_profile_start_checks_read_known_fields():
    profile = load_profile("pmdg_737_900")
    if not profile.source.size:
        pytest.skip("PMDG 737-900 non installé : tables du SDK indisponibles")
    readable = set(profile.source.decode(bytes(profile.source.size)))
    for item in profile.data["depart"]["verifications"]:
        assert "simvar" in item or item["cle"] in readable, item["id"]
        assert item["libelle"]["fr"] and item["conseil"]["en"]


def test_reset_refuses_unknown_state():
    profile = load_profile("pmdg_737_900")
    assert [s["id"] for s in profile.data["depart"]["etats"]] == ["COLDDARK", "COLDDARKGPU"]
    with pytest.raises(depart.DepartError) as error:
        depart.reset(FakeCdu(), profile, "HANGAR", settle_s=0)
    assert error.value.code == "etat_inconnu"


# Page des départs : relevée sur le CDU réel. Page RTE avec LOAD : supposée (position de LOAD non relevée).
DEPARTURES = ["   LFBO DEPARTURES  1/2 ", " SIDS    RTE 1   RUNWAYS", "AMOL7B       <SEL>   32L", "",
              "FINO7B", "", "FIST7B", "", "GAUD7B", "", "LACO7B", "-" * 24, "<INDEX            ROUTE>", "ROUTE DATA UPLINK"]
UPLINK = ["      RTE 1         1/3 ", " ORIGIN             DEST", "LFBO                LFPO", " CO ROUTE        FLT NO.",
          "-----           --------", " ROUTE REQUEST          ", "              LOAD>     ".rjust(24), "", "", "", "", "",
          "<RTE 2         ACTIVATE>", "ROUTE UPLINK READY"]


def test_ring_follows_cdu_text_and_simbrief_plan(monkeypatch, tmp_path):
    from flowxav import settings
    from flowxav.guide import cdu_target
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path))
    settings.update("simbrief", {"dernier_plan": {"piste_depart": "32L", "sid_ident": "FIST7B"}})
    profile = load_profile("pmdg_737_900")
    assert cdu_target(profile, {"cible_cdu": {"texte": "{piste_depart}"}}, DEPARTURES) == "EVT_CDU_L_R1"
    assert cdu_target(profile, {"cible_cdu": {"texte": "{sid_ident}"}}, DEPARTURES) == "EVT_CDU_L_L3"
    assert cdu_target(profile, {"cible_cdu": {"texte": "LOAD"}}, UPLINK) == "EVT_CDU_L_R3"
    assert cdu_target(profile, {"cible_cdu": {"texte": "LOAD"}}, DEPARTURES) is None      # repli sur la commande
    assert cdu_target(profile, {"cible_cdu": {"texte": "{inconnu}"}}, DEPARTURES) is None


def test_simulator_reads_cdu_screen_lines():
    """Régression : la lecture de l'écran du CDU doit renvoyer ses 14 lignes (et jamais None)."""
    from flowxav.simulator import Simulator
    lines = Simulator._read_cdu(FakeCdu(), load_profile("pmdg_737_900"))
    assert isinstance(lines, list) and len(lines) == 14 and "STATE LOAD" in lines[4]
