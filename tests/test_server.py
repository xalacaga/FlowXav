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
    return TestClient(create_app(simulator, guide), base_url="http://127.0.0.1")


def test_status_and_profiles():
    with make_client() as client:
        assert client.get("/api/status").json()["application"] == "FlowXav"
        profiles = client.get("/api/profils").json()
        assert profiles[0]["id"] == "pmdg_737_900" and profiles[0]["commandes"] >= 200
        controls = client.get("/api/profils/pmdg_737_900/commandes").json()
        assert any(c["id"] == "ELEC_BatSelector" for c in controls)
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert client.get("/openapi.json").status_code == 404


def test_guide_flow():
    with make_client() as client:
        client.get("/")
        state = client.post("/api/guide/demarrer", json={"profil": "pmdg_737_900",
                                                         "procedure": "demo_mise_sous_tension"}).json()
        assert state["actif"] and state["total"] == 7
        assert client.post("/api/guide/suivante").json()["index"] == 1
        assert client.post("/api/guide/arreter").json()["actif"] is False
        assert client.post("/api/guide/demarrer", json={"profil": "pmdg_737_900", "procedure": "x"}).status_code == 404
        assert client.post("/api/montrer", json={"profil": "pmdg_737_900", "commande": "nope"}).status_code == 404


def test_local_api_rejects_csrf_rebinding_and_invalid_payloads():
    with make_client() as client:
        assert client.post("/api/guide/arreter").status_code == 403
        assert client.get("/api/status", headers={"Host": "flowxav.attacker.example"}).status_code == 400
        client.get("/")
        assert client.post("/api/guide/arreter", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post("/api/camera/zoom", content='{"pas":NaN}',
                           headers={"Content-Type": "application/json"}).status_code == 422
        assert client.post("/api/camera/zoom", json={"pas": 1, "inattendu": True}).status_code == 422
        assert client.post("/api/camera/zoom", content=b" " * (64 * 1024 + 1),
                           headers={"Content-Type": "application/json"}).status_code == 413
        assert client.post("/api/guide/arreter", headers={"Origin": "http://127.0.0.1"}).status_code == 200
