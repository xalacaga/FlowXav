"""Cohérence des profils d'avion : chaque commande montrée doit avoir une vue et un point valides."""

import json
import math

import pytest

from flowxav.aircraft import aircraft_root, detect, list_profiles, load_profile
from flowxav.state.pmdg import PmdgClientData


def test_at_least_one_profile():
    assert [p.id for p in list_profiles()] == ["pmdg_737_900"] or len(list_profiles()) >= 1


@pytest.mark.parametrize("profile", list_profiles(), ids=lambda p: p.id)
def test_every_control_has_a_valid_view_and_point(profile):
    carte = profile.carte
    assert carte["commandes"], "carte vide"
    for key, control in carte["commandes"].items():
        assert control["vue"] in carte["vues"], f"{key} : vue inconnue {control['vue']}"
        assert 0 <= control["u"] <= 1 and 0 <= control["v"] <= 1, f"{key} : point hors écran"
        assert control["libelle"] and control["libelle"] != key, f"{key} : libellé manquant"


@pytest.mark.parametrize("profile", list_profiles(), ids=lambda p: p.id)
def test_views_are_plausible(profile):
    for name, view in profile.carte["vues"].items():
        pose = profile.view(name)
        assert 0.2 < pose.fov < 2.0, f"{name} : champ de vision en radians attendu"
        assert -90 <= pose.pitch <= 90


@pytest.mark.parametrize("profile", list_profiles(), ids=lambda p: p.id)
def test_procedures_reference_mapped_controls(profile):
    for procedure in profile.procedures():
        assert procedure["etapes"], procedure["id"]
        for step in procedure["etapes"]:
            assert step.get("sans_cible") or step["commande"] in profile.carte["commandes"], f"{procedure['id']}/{step['id']}"
            assert step["titre"]["fr"] and step["titre"]["en"]


def pmdg_source():
    """Source PMDG, tirée du SDK de l'avion installé ; test sauté si l'avion n'est pas installé ici."""
    profile = load_profile("pmdg_737_900")
    if not isinstance(profile.source, PmdgClientData):
        pytest.skip("PMDG 737-900 non installé : tables du SDK indisponibles")
    return profile


def test_pmdg_layout_matches_flight_validated_size():
    source = pmdg_source().source
    assert source.size == 916                     # relevé en vol sur le 737-900 BW
    assert source.fields["AircraftModel"][1] == "unsigned short"


def test_pmdg_procedure_controls_have_state_fields():
    """Une étape validée par l'état doit viser un champ que la source sait lire."""
    profile = pmdg_source()
    readable = set(profile.source.decode(bytes(profile.source.size)))
    readable.update(name for name, _unit in profile.data.get("suivi_vol", {}).get("simvars", []))
    for procedure in profile.procedures():
        for step in procedure["etapes"]:
            if "attendu" in step:
                assert (step.get("verifier") or step.get("commande")) in readable, step["id"]


def test_detection_by_title():
    assert detect("737-900 PAX BW SC").id == "pmdg_737_900"          # titre réel dans MSFS 2024
    assert detect("PMDG 737-900 PMDG House (N739BW | 2022)").id == "pmdg_737_900"
    assert detect("Airbus A320neo Asobo") is None


def test_no_publisher_file_is_shipped_with_the_profile():
    """Contrat PMDG § 1.n/1.p : ni en-tête du SDK ni table qui en est tirée dans le dépôt."""
    folder = aircraft_root() / "pmdg_737_900"
    assert not list(folder.glob("pmdg_ng3_*.json")) and not list(folder.rglob("*.h"))


def test_sdk_tables_are_generated_from_a_header(tmp_path, monkeypatch):
    """Génération au premier lancement, sur un en-tête synthétique (aucun fichier de l'éditeur)."""
    from flowxav import packages, sdk
    header = tmp_path / "MSFS" / "Community" / "pmdg-aircraft-739" / "Documentation" / "SDK"
    header.mkdir(parents=True)
    (header / "PMDG_NG3_SDK.h").write_text(
        "#define EVT_A (THIRD_PARTY_EVENT_ID_MIN + 1)\n#define EVT_B EVT_A\n"
        "struct PMDG_NG3_Data\n{\n bool X; // commentaire\n unsigned short Y[2];\n float Z;\n char reserved[3];\n};\n",
        encoding="latin-1")
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(packages, "packages_roots", lambda: [tmp_path / "MSFS"])
    sdk.forget()
    try:
        tables = sdk.tables(load_profile("pmdg_737_900"))
        assert tables["evenements"] == {"EVT_A": 0x11001, "EVT_B": 0x11001}
        assert tables["disposition"]["champs"] == {"X": [0, "bool", 1], "Y": [2, "unsigned short", 2], "Z": [8, "float", 1]}
        assert tables["disposition"]["taille"] == 16
        assert (tmp_path / "data" / "sdk" / "pmdg_737_900.json").is_file()       # gardées en cache
    finally:
        sdk.forget()


def test_waits_and_alerts_read_known_fields():
    """Compte à rebours et alertes d'étape : leurs champs doivent exister dans l'état de l'avion."""
    profile = pmdg_source()
    readable = set(profile.source.decode(bytes(profile.source.size)))
    for procedure in profile.procedures():
        for step in procedure["etapes"]:
            if "attente" in step:
                assert step["attente"]["debut"]["cle"] in readable and step["attente"]["duree_s"] > 0, step["id"]
            for alert in step.get("alertes", []):
                assert alert["cle"] in readable and alert["message"]["fr"] and alert["message"]["en"], step["id"]


def test_left_and_right_efis_controls_stay_on_their_side():
    """Sélecteurs couplés (un clic change [0] et [1]) : la carte ne doit pas faire la moyenne des deux côtés."""
    commandes = load_profile("pmdg_737_900").carte["commandes"]
    for key, control in commandes.items():
        if key.startswith("EFIS_") and key.endswith("[0]"):
            assert control["u"] < 0.5, key
        if key.startswith("EFIS_") and key.endswith("[1]"):
            assert control["u"] > 0.5, key


def test_profile_loader_rejects_duplicate_keys_escape_and_non_finite_values(tmp_path, monkeypatch):
    import flowxav.aircraft as aircraft

    root = tmp_path / "aircraft"
    root.mkdir()
    monkeypatch.setattr(aircraft, "aircraft_root", lambda: root)
    duplicate = root / "duplicate.json"
    duplicate.write_text('{"nom":"A","nom":"B"}', encoding="utf-8")
    with pytest.raises(aircraft.ProfileDataError, match="dupliquée"):
        aircraft._read_json(duplicate)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(aircraft.ProfileDataError, match="hors du paquet"):
        aircraft._read_json(outside)
    with pytest.raises(aircraft.ProfileDataError, match="Nombre fini"):
        aircraft._validate_map({"vues": {"x": {"position": [0, 0, 0], "pbh": [0, 0, 0], "fov": math.nan}},
                                "commandes": {}}, "test")


def test_pmdg_layout_rejects_fields_outside_payload(tmp_path):
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"taille": 4, "champs": {"bad": [4, "unsigned int", 1]}}), encoding="utf-8")
    with pytest.raises(ValueError, match="hors limites"):
        PmdgClientData(layout, "TEST")
