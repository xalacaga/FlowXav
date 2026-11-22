"""Cohérence des profils d'avion : chaque commande montrée doit avoir une vue et un point valides."""

import json

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
            assert step["commande"] in profile.carte["commandes"], f"{procedure['id']}/{step['id']}"
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
    for procedure in profile.procedures():
        for step in procedure["etapes"]:
            if "attendu" in step:
                assert step.get("verifier", step["commande"]) in readable, step["id"]


def test_detection_by_title():
    assert detect("737-900 PAX BW SC").id == "pmdg_737_900"          # titre réel dans MSFS 2024
    assert detect("PMDG 737-900 PMDG House (N739BW | 2022)").id == "pmdg_737_900"
    assert detect("Airbus A320neo Asobo") is None


def test_layout_file_is_valid_json():
    path = aircraft_root() / "pmdg_737_900" / "pmdg_ng3_layout.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    offsets = [offset for offset, _type, _count in data["champs"].values()]
    assert offsets == sorted(offsets)
