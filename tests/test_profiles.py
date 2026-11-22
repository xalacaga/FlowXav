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


def test_pmdg_layout_matches_flight_validated_size():
    profile = load_profile("pmdg_737_900")
    source = profile.source
    assert isinstance(source, PmdgClientData)
    assert source.size == 916                     # relevé en vol sur le 737-900 BW
    assert source.fields["AircraftModel"][1] == "unsigned short"


def test_pmdg_procedure_controls_have_state_fields():
    """Une étape validée par l'état doit viser un champ que la source sait lire."""
    profile = load_profile("pmdg_737_900")
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


def test_layout_file_is_valid_json():
    path = aircraft_root() / "pmdg_737_900" / "pmdg_ng3_layout.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    offsets = [offset for offset, _type, _count in data["champs"].values()]
    assert offsets == sorted(offsets)


def test_waits_and_alerts_read_known_fields():
    """Compte à rebours et alertes d'étape : leurs champs doivent exister dans l'état de l'avion."""
    profile = load_profile("pmdg_737_900")
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
