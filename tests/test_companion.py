"""Second écran : appairage, cloisonnement du service distant, sans réseau réel."""

from fastapi.testclient import TestClient

from flowxav.companion import MAX_ATTEMPTS, Pairing
from flowxav.server import create_app
from tests.test_server import InertSimulator
from tests.test_guide import Recorder

HOST = "192.168.1.50"


class FakeCompanion:
    def __init__(self):
        self.host, self.port, self.pairing = HOST, 8800, Pairing()


def remote_client():
    from flowxav.guide import Guide
    simulator = InertSimulator()
    guide = Guide(simulator, camera=Recorder(), overlay=Recorder())
    companion = FakeCompanion()
    return TestClient(create_app(simulator, guide, remote=companion), base_url=f"http://{HOST}:8800"), companion


def test_pairing_code_rotates_after_too_many_failures():
    pairing = Pairing()
    first = pairing.code
    for _ in range(MAX_ATTEMPTS):
        assert pairing.pair("000000" if first != "000000" else "111111") is None
    assert pairing.code != first          # nouveau code à relire sur le PC
    token = pairing.pair(pairing.code)
    assert token and pairing.valid(token) and not pairing.valid("faux")


def test_remote_screen_requires_pairing_then_works():
    client, companion = remote_client()
    with client:
        assert client.get("/api/etat").status_code == 401
        assert client.post("/api/appairage", json={"code": "12345"}).status_code == 422
        wrong = "000000" if companion.pairing.code != "000000" else "111111"
        assert client.post("/api/appairage", json={"code": wrong}).status_code == 403
        assert client.post("/api/appairage", json={"code": companion.pairing.code}).status_code == 200
        state = client.get("/api/etat").json()
        assert state["distant"] is True
        assert client.post("/api/guide/arreter").status_code == 200          # pilote le guidage
        assert client.get("/api/simbrief").status_code == 403                # jamais l'identifiant SimBrief
        assert client.post("/api/depart/reinitialiser", json={"profil": "pmdg_737_900"}).status_code == 403


def test_remote_screen_refuses_other_hosts_and_local_refuses_lan_host():
    client, _companion = remote_client()
    with client:
        assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400
    from tests.test_server import make_client
    with make_client() as local:
        assert local.get("/api/status", headers={"host": f"{HOST}:8790"}).status_code == 400
