"""Import SimBrief : sans réseau, avec une réponse simulée."""

import json

import pytest

from flowxav import simbrief

OFP = {
    "fetch": {"status": "Success"},
    "params": {"units": "kgs"},
    "general": {"icao_airline": "ABF", "flight_number": "059", "route": "FISTO UN872 ERIGA",
                "initial_altitude": "35000", "costindex": "30"},
    "origin": {"icao_code": "LFBO", "plan_rwy": "14R"},
    "destination": {"icao_code": "LFPO", "plan_rwy": "06"},
    "weights": {"est_zfw": "61234"},
    "fuel": {"reserve": "1520", "plan_ramp": "7840"},
    "navlog": {"fix": [{"ident": "FISTO", "via_airway": "FISTO5A", "is_sid_star": "1", "stage": "CLB"},
                       {"ident": "ERIGA", "via_airway": "UN872", "is_sid_star": "0", "stage": "CRZ"}]},
    "fms_downloads": {"directory": "https://www.simbrief.com/ofp/flightplans/",
                      "pdf": {"name": "PDF Document", "link": "LFBOLFPO_PDF.pdf"},
                      "pmr": {"name": "PMDG", "link": "LFBOLFPO_PMR_1.rte"},
                      "pmw": {"name": "PMDG Wind Data", "link": "LFBOLFPO_WX.rte"}},
}


def fake_get(responses):
    calls = []

    def get(url):
        calls.append(url)
        for prefix, answer in responses.items():
            if url.startswith(prefix):
                return answer
        return 404, b""
    get.calls = calls
    return get


def test_import_writes_route_and_summarises(tmp_path):
    get = fake_get({simbrief.FETCH_URL: (200, json.dumps(OFP).encode()),
                    "https://www.simbrief.com/ofp/flightplans/LFBOLFPO_PMR_1.rte": (200, b"ROUTE PMDG")})
    result = simbrief.import_latest("pilote", {"format": "pmdg_rte", "dossiers": [str(tmp_path)]}, get)
    assert (tmp_path / "LFBOLFPO.rte").read_bytes() == b"ROUTE PMDG"
    assert result["fichier"] == "LFBOLFPO.rte" and result["vol"] == "ABF059"
    assert result["niveau"] == "FL350" and result["cost_index"] == "30" and result["sid"] == "FISTO5A"
    assert result["zfw"] == "61.2" and result["reserve"] == "1.5" and result["unites"] == "KGS"
    assert "username=pilote" in get.calls[0] and "json=v2" in get.calls[0]


def test_numeric_identifier_is_a_pilot_id(tmp_path):
    get = fake_get({simbrief.FETCH_URL: (200, json.dumps(OFP).encode()), "https://www.simbrief.com/ofp": (200, b"x")})
    simbrief.import_latest("1234567", {"dossiers": [str(tmp_path)]}, get)
    assert "userid=1234567" in get.calls[0]


def test_unknown_user_and_missing_folder(tmp_path):
    get = fake_get({simbrief.FETCH_URL: (400, b'{"fetch":{"status":"Error: Unknown UserID"}}')})
    with pytest.raises(simbrief.SimbriefError) as error:
        simbrief.import_latest("personne", {"dossiers": [str(tmp_path)]}, get)
    assert error.value.code == "inconnu"
    with pytest.raises(simbrief.SimbriefError) as error:
        simbrief.import_latest("pilote", {"dossiers": [str(tmp_path / "absent")]}, get)
    assert error.value.code == "dossier_absent"
    with pytest.raises(simbrief.SimbriefError) as error:
        simbrief.fetch_ofp("  ", get)
    assert error.value.code == "identifiant_vide"
    with pytest.raises(simbrief.SimbriefError) as error:
        simbrief.fetch_ofp("x" * 129, get)
    assert error.value.code == "identifiant_invalide"


def test_download_rejects_non_simbrief_or_non_https_urls():
    for directory in ("http://www.simbrief.com/ofp/", "https://attacker.example/ofp/"):
        ofp = dict(OFP, fms_downloads={"directory": directory,
                                      "pmr": {"name": "PMDG", "link": "route.rte"}})
        with pytest.raises(simbrief.SimbriefError) as error:
            simbrief.download(ofp, "pmdg_rte", "route")
        assert error.value.code == "adresse_refusee"
    with pytest.raises(simbrief.SimbriefError) as error:
        simbrief.download(OFP, "format-inconnu", "route")
    assert error.value.code == "format_inconnu"


def test_identifier_is_saved_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWXAV_DATA_DIR", str(tmp_path))
    assert simbrief.load_identifier() == ""
    simbrief.save_identifier("pilote")
    assert simbrief.load_identifier() == "pilote"


def test_real_v2_shape_navlog_list_sid_fields_and_wind(tmp_path):
    """Structure relevée sur un OFP réel : navlog = liste, SID dans general, vents « pmw »."""
    ofp = dict(OFP, navlog=[{"ident": "FISTO", "via_airway": "X", "is_sid_star": "1", "stage": "CLB"}])
    ofp["general"] = dict(OFP["general"], sid_ident="FISTO5A", sid_trans="")
    ofp["origin"] = dict(OFP["origin"], trans_alt="5000")
    ofp["fms_downloads"] = dict(OFP["fms_downloads"], pmr={"name": "PMDG Flightplan", "link": "A_1.rte"},
                                pmw={"name": "PMDG Wind Uplink", "link": "A_1.wx"})
    plans, wx = tmp_path / "Flightplans", tmp_path / "WX"
    plans.mkdir(); wx.mkdir()
    get = fake_get({simbrief.FETCH_URL: (200, json.dumps(ofp).encode()),
                    "https://www.simbrief.com/ofp/flightplans/A_1.rte": (200, b"RTE"),
                    "https://www.simbrief.com/ofp/flightplans/A_1.wx": (200, b"WX")})
    result = simbrief.import_latest("pilote", {"format": "pmdg_rte", "dossiers": [str(plans)]}, get)
    assert result["sid"] == "FISTO5A" and result["altitude_transition"] == "5000"
    assert (plans / "LFBOLFPO.rte").read_bytes() == b"RTE" and (wx / "LFBOLFPO.wx").read_bytes() == b"WX"
    assert result["vents"] == "LFBOLFPO.wx"
