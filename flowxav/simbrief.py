"""Import du dernier plan de vol SimBrief de l'utilisateur dans son avion.

Sur action de l'utilisateur uniquement (bouton « Importer ») : l'API SimBrief
demande de ne jamais l'interroger en boucle. FlowXav récupère le dernier OFP,
télécharge le fichier de route au format de l'avion fourni par SimBrief (pour
le PMDG : .rte) et le dépose dans le dossier où le FMC le cherche
(RTE > FLT PLAN REQUEST). Il renvoie aussi les chiffres utiles pour le FMC.

L'identifiant SimBrief est une donnée personnelle : il est enregistré dans les
réglages locaux et n'apparaît jamais dans le journal.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from flowxav import __version__, settings

LOGGER = logging.getLogger(__name__)
FETCH_URL = "https://www.simbrief.com/api/xml.fetcher.php"
CLIENT = f"FlowXav-{__version__}"
TIMEOUT_S = 20
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SimbriefError(Exception):
    """Erreur présentable : `code` est traduit par l'interface."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


def _validate_url(url: str) -> str:
    """N'autorise que HTTPS vers SimBrief, y compris après une redirection."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SimbriefError("adresse_refusee") from error
    host = (parsed.hostname or "").lower()
    allowed_host = host == "simbrief.com" or host.endswith(".simbrief.com")
    if (parsed.scheme != "https" or not allowed_host or parsed.username is not None
            or parsed.password is not None or port not in {None, 443}):
        raise SimbriefError("adresse_refusee")
    return url


def _read_limited(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
        raise SimbriefError("reponse_trop_grande")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise SimbriefError("reponse_trop_grande")
    return body


# ------------------------------------------------------------------ réglages
def load_identifier() -> str:
    return str(settings.section("simbrief").get("identifiant", ""))


def save_identifier(identifier: str) -> None:
    settings.update("simbrief", {"identifiant": identifier})


# ------------------------------------------------------------------ réseau
def _get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(_validate_url(url), headers={"User-Agent": CLIENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            _validate_url(response.geturl())
            return response.status, _read_limited(response)
    except urllib.error.HTTPError as error:
        _validate_url(error.geturl())
        return error.code, _read_limited(error)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SimbriefError("reseau", str(error)) from error


def fetch_ofp(identifier: str, get=_get) -> dict[str, Any]:
    """Dernier OFP de l'utilisateur (alias Navigraph, ou Pilot ID numérique)."""
    identifier = identifier.strip()
    if not identifier:
        raise SimbriefError("identifiant_vide")
    if len(identifier) > 128:
        raise SimbriefError("identifiant_invalide")
    key = "userid" if identifier.isdigit() else "username"
    query = urllib.parse.urlencode({key: identifier, "json": "v2", "client": CLIENT})
    status, body = get(f"{FETCH_URL}?{query}")
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError as error:
        raise SimbriefError("reponse_invalide") from error
    fetch_status = str(data.get("fetch", {}).get("status", ""))
    if status != 200 or not fetch_status.lower().startswith("success"):
        code = "inconnu" if "unknown" in fetch_status.lower() or "userid" in fetch_status.lower() else "refus"
        raise SimbriefError(code, fetch_status)
    return data


# ------------------------------------------------------------------ lecture de l'OFP
def _text(value: Any) -> str:
    if isinstance(value, dict):          # champ vide renvoyé comme {} en JSON v2
        return ""
    return str(value or "").strip()


def _thousands(value: Any) -> str:
    try:
        return f"{float(_text(value)) / 1000:.1f}"
    except ValueError:
        return ""


def _qnh(metar: str) -> str:
    """Calage altimétrique lu dans le METAR : « Q1018 » → « 1018 hPa », « A2992 » → « 29.92 inHg »."""
    found = re.search(r"\bQ(\d{4})\b", metar)
    if found:
        return f"{int(found.group(1))} hPa"
    found = re.search(r"\bA(\d{4})\b", metar)
    return f"{int(found.group(1)) / 100:.2f} inHg" if found else ""


def _feet(value: Any) -> str:
    try:
        return str(round(float(_text(value))))
    except ValueError:
        return ""


def _section(ofp: dict[str, Any], key: str) -> dict[str, Any]:
    value = ofp.get(key)
    return value if isinstance(value, dict) else {}


def summary(ofp: dict[str, Any]) -> dict[str, Any]:
    """Ce qu'il faut entrer dans le FMC, lu dans l'OFP (structure JSON v2 relevée sur un OFP réel)."""
    general, origin, dest = _section(ofp, "general"), _section(ofp, "origin"), _section(ofp, "destination")
    weights, fuel = _section(ofp, "weights"), _section(ofp, "fuel")
    units = _text(_section(ofp, "params").get("units")).upper() or "KGS"
    altitude = _text(general.get("initial_altitude"))
    level = f"FL{int(altitude) // 100:03d}" if altitude.isdigit() else altitude
    sid = " ".join(filter(None, (_text(general.get("sid_ident")), _text(general.get("sid_trans")))))
    if not sid:
        # Repli : « navlog » est la liste des points (un objet {"fix": [...]} dans d'anciens formats).
        navlog = ofp.get("navlog")
        fixes = navlog.get("fix", []) if isinstance(navlog, dict) else navlog if isinstance(navlog, list) else []
        sid = next((_text(f.get("via_airway")) for f in fixes if isinstance(f, dict)
                    and _text(f.get("is_sid_star")) == "1" and _text(f.get("stage")) == "CLB"), "")
    return {
        "origine": _text(origin.get("icao_code")), "destination": _text(dest.get("icao_code")),
        "vol": _text(general.get("icao_airline")) + _text(general.get("flight_number")),
        "route": _text(general.get("route")),
        "niveau": level, "altitude_croisiere": altitude,
        "cost_index": _text(general.get("costindex")),
        "piste_depart": _text(origin.get("plan_rwy")), "piste_arrivee": _text(dest.get("plan_rwy")),
        "sid": sid,
        "sid_ident": _text(general.get("sid_ident")) or sid.split(" ")[0],
        "altitude_transition": _text(origin.get("trans_alt")).lstrip("0") or _text(origin.get("trans_alt")),
        "crz_fmc": str(int(altitude) // 100) if altitude.isdigit() else "",
        "qnh": _qnh(_text(origin.get("metar"))),
        "elevation_origine": _feet(origin.get("elevation")),
        "elevation_destination": _feet(dest.get("elevation")),
        "unites": units,
        "zfw": _thousands(weights.get("est_zfw")),
        "reserve": _thousands(fuel.get("reserve")),
        "carburant_bloc": _thousands(fuel.get("plan_ramp")),
    }


# Téléchargements FMS de l'OFP (relevés sur un OFP réel) : clé SimBrief, et repli sur le nom.
FORMATS = {
    "pmdg_rte": {"route": ("pmr", "pmdg flightplan", ".rte"), "vent": ("pmw", "pmdg wind uplink", ".wx")},
}


def download(ofp: dict[str, Any], format_name: str, kind: str) -> str | None:
    """URL du fichier demandé (« route » ou « vent ») au format de l'avion, ou None."""
    downloads = _section(ofp, "fms_downloads")
    directory = _text(downloads.get("directory"))
    if format_name not in FORMATS or kind not in FORMATS[format_name]:
        raise SimbriefError("format_inconnu")
    key, name_hint, extension = FORMATS[format_name][kind]
    preferred = downloads.get(key)
    for entry in [preferred] + list(downloads.values()):
        if not isinstance(entry, dict):
            continue
        name, link = _text(entry.get("name")).lower(), _text(entry.get("link"))
        if (entry is preferred or name_hint in name) and link.lower().endswith(extension):
            return _validate_url(urllib.parse.urljoin(directory, link))
    return None


# ------------------------------------------------------------------ dépôt dans l'avion
def flight_plan_folder(candidates: list[str]) -> Path | None:
    """Premier dossier de plans de vol de l'avion qui existe sur ce PC."""
    for candidate in candidates:
        path = Path(os.path.expandvars(candidate))
        if "%" not in str(path) and path.is_dir():
            return path
    return None


def import_latest(identifier: str, plan_config: dict[str, Any], get=_get) -> dict[str, Any]:
    """Récupère le dernier OFP et dépose la route dans le dossier de l'avion."""
    folder = flight_plan_folder(plan_config.get("dossiers", []))
    if folder is None:
        raise SimbriefError("dossier_absent")
    ofp = fetch_ofp(identifier, get)
    info = summary(ofp)
    format_name = plan_config.get("format", "pmdg_rte")
    if format_name not in FORMATS:
        raise SimbriefError("format_inconnu")
    url = download(ofp, format_name, "route")
    if url is None:
        raise SimbriefError("pas_de_route")
    status, body = get(url)
    if status != 200 or not body.strip():
        raise SimbriefError("telechargement", f"HTTP {status}")
    stem = re.sub(r"[^A-Z0-9]", "", (info["origine"] + info["destination"]).upper()) or "SIMBRIEF"
    target = folder / f"{stem}{FORMATS[format_name]['route'][2]}"
    _write_bytes_atomic(target, body)
    LOGGER.info("Plan SimBrief importé (%d octets)", len(body))
    wind = _save_wind(ofp, format_name, folder.parent / "WX", stem, get)
    settings.update("simbrief", {"dernier_plan": info})       # piste, SID… pour viser la bonne touche du CDU
    return {**info, "fichier": target.name, "vents": wind}


def _save_wind(ofp: dict[str, Any], format_name: str, folder: Path, stem: str, get) -> str:
    """Vents prévus pour le FMC (facultatif : un échec ne bloque pas l'import de la route)."""
    url = download(ofp, format_name, "vent")
    if url is None or not folder.is_dir():
        return ""
    try:
        status, body = get(url)
    except SimbriefError:
        return ""
    if status != 200 or not body.strip():
        return ""
    target = folder / f"{stem}{FORMATS[format_name]['vent'][2]}"
    _write_bytes_atomic(target, body)
    return target.name


def _write_bytes_atomic(target: Path, body: bytes) -> None:
    """Remplace le fichier sans suivre un éventuel lien symbolique portant son nom."""
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=f".{target.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
