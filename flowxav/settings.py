"""Réglages locaux de l'utilisateur (data/reglages.json en développement, %LOCALAPPDATA%\\FlowXav installé).

Un seul fichier JSON, rangé par sections (« simbrief », « durees »…). Jamais
journalisé : il peut contenir des identifiants personnels.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import Any

from flowxav.paths import user_data_path

FILE = "reglages.json"
_LOCK = threading.Lock()


def load() -> dict[str, Any]:
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
