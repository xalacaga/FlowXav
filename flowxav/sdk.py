"""Tables tirées du SDK de l'éditeur de l'avion, générées chez l'utilisateur au premier lancement.

FlowXav ne livre aucun fichier de l'éditeur (contrat PMDG § 1.n et 1.p) : ni l'en-tête C du SDK,
ni les tables qui en sont tirées. Au premier lancement, il lit l'en-tête fourni avec l'avion
installé chez l'utilisateur et en tire :

- la disposition de la zone d'état (nom, décalage, type, nombre de chaque champ) ;
- la table des événements de commande (nom → numéro).

Les deux sont gardées en cache dans les données locales de FlowXav, avec l'empreinte de
l'en-tête : une mise à jour de l'avion les fait régénérer.

Alignement naturel de MSVC (empaquetage par défaut) : chaque champ est aligné sur sa taille,
la structure sur son plus grand champ. Pour le 737-900 BW, 916 octets, vérifié en vol.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from flowxav import packages
from flowxav.paths import user_data_path

LOGGER = logging.getLogger(__name__)
MAX_HEADER_BYTES = 2 * 1024 * 1024
SIZES = {"bool": 1, "char": 1, "unsigned char": 1, "short": 2, "unsigned short": 2,
         "int": 4, "unsigned int": 4, "float": 4, "double": 8}
DECL = re.compile(r"(unsigned (?:char|short|int)|bool|char|short|int|float|double) (\w+)(?:\[(\d+)\])?$")
THIRD_PARTY_EVENT_ID_MIN = 0x00011000
EVENT = re.compile(r"^\s*#define\s+(EVT_\w+)\s+\(\s*THIRD_PARTY_EVENT_ID_MIN\s*\+\s*(\d+)\s*\)", re.M)
ALIAS = re.compile(r"^\s*#define\s+(EVT_\w+)\s+(EVT_\w+)\s*$", re.M)
_LOCK = threading.Lock()
_MEMORY: dict[str, dict[str, Any]] = {}


class SdkMissing(Exception):
    """En-tête du SDK introuvable dans l'installation de l'avion, et aucune table en cache."""


def parse_layout(header: str, structure: str) -> dict[str, Any]:
    """Disposition d'une structure C : {"structure", "taille", "champs": {nom: [décalage, type, nombre]}}."""
    body = header[header.index(f"struct {structure}"):]
    body = re.sub(r"//[^\n]*", "", body[body.index("{") + 1: body.index("};")])
    fields, offset, max_align = {}, 0, 1
    for raw in body.split(";"):
        decl = " ".join(raw.split())
        if not decl:
            continue
        match = DECL.match(decl)
        if not match:
            raise ValueError(f"déclaration non comprise : {decl!r}")
        ctype, name, count = match.group(1), match.group(2), int(match.group(3) or 1)
        size = SIZES[ctype]
        offset = (offset + size - 1) // size * size
        max_align = max(max_align, size)
        if name != "reserved":
            fields[name] = [offset, ctype, count]
        offset += size * count
    total = (offset + max_align - 1) // max_align * max_align
    return {"structure": structure, "taille": total, "champs": fields}


def parse_events(header: str) -> dict[str, int]:
    """Événements « #define EVT_X (THIRD_PARTY_EVENT_ID_MIN + n) » et leurs alias."""
    events = {name: THIRD_PARTY_EVENT_ID_MIN + int(offset) for name, offset in EVENT.findall(header)}
    for _ in range(3):                                  # alias d'alias
        for name, target in ALIAS.findall(header):
            if target in events and name not in events:
                events[name] = events[target]
    return dict(sorted(events.items()))


def _cache_path(profile_id: str) -> Path:
    return user_data_path("sdk", f"{profile_id}.json")


def tables(profile) -> dict[str, Any]:
    """{"disposition": …, "evenements": …} pour l'avion du profil ; générées puis gardées en cache."""
    config = profile.data.get("sdk")
    if not config:
        raise SdkMissing("Ce profil n'utilise pas de SDK.")
    with _LOCK:
        if profile.id in _MEMORY:
            return _MEMORY[profile.id]
        cached = _read_cache(profile.id)
        header_path = packages.find_file(config["paquets"], config["entete"], packages.packages_roots())
        if header_path is None:
            if cached:
                _MEMORY[profile.id] = cached               # avion momentanément introuvable : dernières tables connues
                return cached
            raise SdkMissing(f"En-tête du SDK introuvable : {config['entete']}")
        raw = header_path.read_bytes()[:MAX_HEADER_BYTES]
        stamp = hashlib.sha256(raw).hexdigest()
        if cached and cached.get("entete_sha256") == stamp:
            _MEMORY[profile.id] = cached
            return cached
        text = raw.decode("latin-1")
        generated = {"entete_sha256": stamp, "disposition": parse_layout(text, config["structure"]),
                     "evenements": parse_events(text)}
        path = _cache_path(profile.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(generated), encoding="utf-8")
        LOGGER.info("Tables du SDK générées pour %s (%d champs, %d événements)", profile.id,
                    len(generated["disposition"]["champs"]), len(generated["evenements"]))
        _MEMORY[profile.id] = generated
        return generated


def _read_cache(profile_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_cache_path(profile_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(data, dict) and isinstance(data.get("disposition"), dict) and isinstance(data.get("evenements"), dict)
    return data if ok else None


def forget() -> None:
    """Oublie les tables en mémoire (tests, changement d'installation)."""
    with _LOCK:
        _MEMORY.clear()
