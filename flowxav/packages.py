"""Paquets MSFS installés chez l'utilisateur : où trouver les fichiers fournis avec un avion.

Certains fichiers ne peuvent pas voyager avec FlowXav (contrat de licence de l'éditeur, en-tête
de son SDK) : FlowXav les lit là où l'avion est installé. Le dossier des paquets est celui de
la ligne InstalledPackagesPath du fichier UserCfg.opt de MSFS 2024.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

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


def find_file(packages: list[str], relative: str, roots: list[Path]) -> Path | None:
    """Fichier `relative` dans le paquet de l'avion : Community*, ou Official*\\<boutique>."""
    for root in roots:
        folders = [p for p in root.glob("Community*") if p.is_dir()]
        folders += [store for official in root.glob("Official*") if official.is_dir()
                    for store in official.iterdir() if store.is_dir()]
        for folder in folders:
            for package in packages:
                path = folder / package / relative
                if path.is_file():
                    return path
    return None
