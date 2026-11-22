"""API locale : profils, commandes, démarrage du guidage — avec un simulateur factice."""

from fastapi.testclient import TestClient

from flowxav.server import create_app
from tests.test_guide import FakeSimulator, Recorder


class InertSimulator(FakeSimulator):
    def start(self):
        pass

    def close(self):
        pass


def make_client():
    from flowxav.guide import Guide

    simulator = InertSimulator()
    guide = Guide(simulator, camera=Recorder(), overlay=Recorder())
    guide.start_thread = lambda: None
    return TestClient(create_app(simulator, guide))


def test_status_and_profiles():
    with make_client() as client:
        assert client.get("/api/status").json()["application"] == "FlowXav"
        profiles = client.get("/api/profils").json()
        assert profiles[0]["id"] == "pmdg_737_900" and profiles[0]["commandes"] >= 200
        controls = client.get("/api/profils/pmdg_737_900/commandes").json()
        assert any(c["id"] == "ELEC_BatSelector" for c in controls)
        assert client.get("/").status_code == 200


def test_guide_flow():
    with make_client() as client:
        state = client.post("/api/guide/demarrer", json={"profil": "pmdg_737_900",
                                                         "procedure": "demo_mise_sous_tension"}).json()
        assert state["actif"] and state["total"] == 7
        assert client.post("/api/guide/suivante").json()["index"] == 1
        assert client.post("/api/guide/arreter").json()["actif"] is False
        assert client.post("/api/guide/demarrer", json={"profil": "pmdg_737_900", "procedure": "x"}).status_code == 404
        assert client.post("/api/montrer", json={"profil": "pmdg_737_900", "commande": "nope"}).status_code == 404
