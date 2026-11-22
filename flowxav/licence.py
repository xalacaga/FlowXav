"""Contrat de licence de l'éditeur de l'avion, à faire accepter avant de piloter l'avion par son SDK.

Le contrat PMDG (§ 1.t) demande à toute application créée avec son SDK d'afficher ce contrat
à l'utilisateur et d'exiger qu'il l'accepte manuellement ; il interdit aussi de redistribuer
les fichiers PMDG (§ 1.n, 1.p). FlowXav ne contient donc pas le texte : il le lit dans
l'installation de l'avion chez l'utilisateur, l'affiche, et retient l'acceptation (empreinte
du texte : un nouveau contrat devra être accepté à nouveau).

Tant que le contrat n'est pas accepté, FlowXav peut guider mais ne manœuvre rien dans l'avion.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from flowxav import settings

MAX_BYTES = 512 * 1024
USER_CFG = (
    r"%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalCache\UserCfg.opt",   # MSFS 2024, Microsoft Store
    r"%APPDATA%\Microsoft Flight Simulator 2024\UserCfg.opt",                                # MSFS 2024, Steam
)


def packages_roots() -> list[Path]:
    """Dossiers des paquets MSFS (InstalledPackagesPath de UserCfg.opt)."""
    roots = []
    for candidate in USER_CFG:
        path = Path(os.path.expandvars(candidate))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r'^\s*InstalledPackagesPath\s+"([^"]+)"', content, re.M)
        if found and Path(found.group(1)).is_dir():
            roots.append(Path(found.group(1)))
    return roots


_FOUND: dict[tuple, Path | None] = {}


def find_text(config: dict[str, Any], roots: list[Path] | None = None) -> Path | None:
    """Fichier du contrat dans le paquet de l'avion ; emplacement retenu (l'interface le demande souvent)."""
    if roots is None:
        key = (config["fichier"], tuple(config.get("paquets", [])))
        if key not in _FOUND or (_FOUND[key] is not None and not _FOUND[key].is_file()):
            _FOUND[key] = _search(config, packages_roots())
        return _FOUND[key]
    return _search(config, roots)


def _search(config: dict[str, Any], roots: list[Path]) -> Path | None:
    """Cherche dans Community*, Official*\\<boutique> de chaque dossier de paquets."""
    for root in roots:
        folders = [p for p in root.glob("Community*") if p.is_dir()]
        folders += [store for official in root.glob("Official*") if official.is_dir()
                    for store in official.iterdir() if store.is_dir()]
        for folder in folders:
            for package in config.get("paquets", []):
                path = folder / package / config["fichier"]
                if path.is_file():
                    return path
    return None


def read(path: Path) -> str:
    """Texte du contrat : UTF-8, sinon Windows-1252 (fichiers .txt des éditeurs, guillemets typographiques)."""
    data = path.read_bytes()[:MAX_BYTES]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def status(profile, roots: list[Path] | None = None) -> dict[str, Any]:
    """Contrat requis ? accepté ? et son texte (lu chez l'utilisateur) s'il faut l'afficher."""
    config = profile.data.get("licence")
    if not config:
        return {"requise": False, "acceptee": True}
    path = find_text(config, roots)
    text = read(path) if path else ""
    accepted = settings.section("licences").get(profile.id)
    ok = bool(accepted) and (not text or accepted == fingerprint(text))
    result = {"requise": True, "acceptee": ok, "editeur": config.get("editeur", ""), "trouve": bool(path)}
    if not ok:
        result["texte"] = text
    return result


def accept(profile, roots: list[Path] | None = None) -> dict[str, Any]:
    """Acceptation manuelle par l'utilisateur (case cochée puis bouton, sur le PC)."""
    config = profile.data.get("licence")
    if not config:
        return {"requise": False, "acceptee": True}
    path = find_text(config, roots)
    settings.update("licences", {profile.id: fingerprint(read(path)) if path else "accepte_sans_texte"})
    return status(profile, roots)


def accepted(profile) -> bool:
    return bool(status(profile)["acceptee"])
