"""Réglages locaux de l'utilisateur (data/reglages.json en développement, %LOCALAPPDATA%\\FlowXav installé).

Un seul fichier JSON, rangé par sections (« simbrief », « durees »…). Jamais
journalisé : il peut contenir des identifiants personnels.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from typing import Any

from flowxav.paths import user_data_path

FILE = "reglages.json"
_LOCK = threading.Lock()


_CACHE: dict[str, Any] = {"stamp": None, "data": {}}


def load() -> dict[str, Any]:
    """Réglages complets ; relus seulement si le fichier a changé (le guidage les consulte souvent)."""
    path = user_data_path(FILE)
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    if _CACHE["stamp"] == stamp:
        return copy.deepcopy(_CACHE["data"])
    data = _read()
    _CACHE.update(stamp=stamp, data=data)
    return copy.deepcopy(data)


def _read() -> dict[str, Any]:
    try:
        data = json.loads(user_data_path(FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def section(name: str) -> dict[str, Any]:
    value = load().get(name, {})
    return value if isinstance(value, dict) else {}


def update(name: str, values: dict[str, Any]) -> None:
    """Fusionne `values` dans la section `name` et enregistre."""
    with _LOCK:
        data = load()
        current = data.get(name) if isinstance(data.get(name), dict) else {}
        current.update(values)
        data[name] = current
        path = user_data_path(FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=1, ensure_ascii=False)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
