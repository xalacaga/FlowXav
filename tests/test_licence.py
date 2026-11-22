"""Contrat de licence de l'éditeur : lu dans l'installation, accepté manuellement, exigé pour piloter l'avion."""

import pytest

from flowxav import licence
from flowxav.actions import NotActionable, plan_step
from flowxav.aircraft import load_profile

PROFILE = load_profile("pmdg_737_900")


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path / "data"))
    eula = tmp_path / "MSFS" / "Community" / "pmdg-aircraft-739" / "Documentation"
    eula.mkdir(parents=True)
    (eula / "END-USER SOFTWARE LICENSE AGREEMENT.txt").write_text("CONTRAT DE TEST v1", encoding="utf-8")
    roots = [tmp_path / "MSFS"]
    monkeypatch.setattr(licence, "packages_roots", lambda: roots)
    licence._FOUND.clear()
    return eula / "END-USER SOFTWARE LICENSE AGREEMENT.txt", roots


def test_agreement_is_read_from_the_installation_and_must_be_accepted(install):
    path, roots = install
    status = licence.status(PROFILE, roots)
    assert status == {"requise": True, "acceptee": False, "editeur": "PMDG", "trouve": True, "texte": "CONTRAT DE TEST v1"}
    assert licence.accept(PROFILE, roots)["acceptee"]
    path.write_text("CONTRAT DE TEST v2", encoding="utf-8")         # nouveau contrat : nouvelle acceptation
    assert not licence.status(PROFILE, roots)["acceptee"]


def test_no_control_of_the_aircraft_before_acceptance(install):
    bat = next(s for s in PROFILE.procedure("mise_sous_tension_apu")["etapes"] if s["id"] == "bat")
    with pytest.raises(NotActionable) as error:
        plan_step(PROFILE, bat, {}, {})
    assert error.value.reason == "licence"
    licence.accept(PROFILE)
    assert plan_step(PROFILE, bat, {}, {}).sends
