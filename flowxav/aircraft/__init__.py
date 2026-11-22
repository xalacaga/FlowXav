"""Profils d'avion : ce qui change d'un appareil à l'autre, rangé dans un dossier par avion.

    flowxav/aircraft/<id>/
        profile.json      identité, reconnaissance par le titre MSFS, source d'état
        carte.json        vues de la caméra et position de chaque commande à l'écran
        procedures/*.json procédures (Cold & Dark → prêt au départ…)

Ajouter un avion (Boeing, Airbus…) revient à ajouter un dossier : le moteur de
guidage, la caméra et la surimpression sont communs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from flowxav.paths import resource_path
from flowxav.sim.simconnect import Pose


def aircraft_root() -> Path:
    return resource_path("flowxav", "aircraft")


@dataclass
class Control:
    key: str
    view: str
    u: float
    v: float
    label: str


@dataclass
class Profile:
    id: str
    folder: Path
    data: dict[str, Any]
    _carte: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def name(self) -> str:
        return self.data["nom"]

    @property
    def manufacturer(self) -> str:
        return self.data.get("constructeur", "")

    @cached_property
    def carte(self) -> dict[str, Any]:
        path = self.folder / "carte.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"vues": {}, "commandes": {}}

    @cached_property
    def source(self):
        from flowxav.state import make_source
        return make_source(self.data["etat"], self.folder)

    def view(self, name: str) -> Pose:
        return Pose.from_dict(self.carte["vues"][name])

    def control(self, key: str) -> Control:
        c = self.carte["commandes"][key]
        return Control(key, c["vue"], c["u"], c["v"], c.get("libelle", key))

    def matches(self, title: str) -> bool:
        lowered = title.lower()
        rule = self.data.get("reconnaissance", {})
        return all(word.lower() in lowered for word in rule.get("titre_contient", [])) and bool(rule)

    def procedures(self) -> list[dict[str, Any]]:
        folder = self.folder / "procedures"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    def procedure(self, procedure_id: str) -> dict[str, Any]:
        for procedure in self.procedures():
            if procedure["id"] == procedure_id:
                return procedure
        raise KeyError(procedure_id)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "nom": self.name, "constructeur": self.manufacturer,
                "commandes": len(self.carte["commandes"]), "vues": len(self.carte["vues"]),
                "procedures": [{"id": p["id"], "titre": p["titre"], "etapes": len(p["etapes"])}
                               for p in self.procedures()]}


def list_profiles() -> list[Profile]:
    profiles = []
    for path in sorted(aircraft_root().glob("*/profile.json")):
        profiles.append(Profile(path.parent.name, path.parent, json.loads(path.read_text(encoding="utf-8"))))
    return profiles


def load_profile(profile_id: str) -> Profile:
    for profile in list_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Profil inconnu : {profile_id}")


def detect(title: str) -> Profile | None:
    """Premier profil dont la règle de reconnaissance correspond au titre de l'avion chargé."""
    return next((profile for profile in list_profiles() if profile.matches(title)), None)
