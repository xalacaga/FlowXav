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
import math
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from flowxav.paths import resource_path
from flowxav.sim.projection import DEFAULT_DEPTH_M
from flowxav.sim.simconnect import Pose

MAX_JSON_BYTES = 5 * 1024 * 1024


class ProfileDataError(ValueError):
    pass


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileDataError(f"Clé JSON dupliquée : {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    root = aircraft_root().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ProfileDataError(f"Ressource avion hors du paquet : {path.name}")
    try:
        if resolved.stat().st_size > MAX_JSON_BYTES:
            raise ProfileDataError(f"Ressource avion trop volumineuse : {path.name}")
        value = json.loads(resolved.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileDataError(f"Ressource avion invalide : {path.name}") from error
    if not isinstance(value, dict):
        raise ProfileDataError(f"Objet JSON attendu : {path.name}")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileDataError(f"Texte requis : {field}")
    return value


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProfileDataError(f"Nombre fini requis : {field}")
    return float(value)


def _validate_profile(data: dict[str, Any], profile_id: str) -> None:
    _text(data.get("nom"), f"{profile_id}.nom")
    recognition = data.get("reconnaissance")
    words = recognition.get("titre_contient") if isinstance(recognition, dict) else None
    if not isinstance(words, list) or not words or any(not isinstance(word, str) or not word for word in words):
        raise ProfileDataError(f"Reconnaissance invalide : {profile_id}")
    state = data.get("etat")
    if not isinstance(state, dict) or state.get("source") not in {"pmdg_ng3", "simvars"}:
        raise ProfileDataError(f"Source d'état invalide : {profile_id}")
    if state["source"] == "pmdg_ng3":
        layout, sdk = state.get("disposition"), data.get("sdk")
        if layout is not None:
            if not isinstance(layout, str) or Path(layout).name != layout or not layout.endswith(".json"):
                raise ProfileDataError(f"Disposition PMDG invalide : {profile_id}")
        elif not (isinstance(sdk, dict) and isinstance(sdk.get("paquets"), list) and sdk["paquets"]
                  and all(isinstance(p, str) and p and Path(p).name == p for p in sdk["paquets"])
                  and isinstance(sdk.get("entete"), str) and ".." not in Path(sdk["entete"]).parts
                  and isinstance(sdk.get("structure"), str) and sdk["structure"].isidentifier()):
            raise ProfileDataError(f"SDK PMDG invalide : {profile_id}")
        _text(state.get("zone"), f"{profile_id}.etat.zone")
    elif not isinstance(state.get("variables"), list):
        raise ProfileDataError(f"Variables SimConnect invalides : {profile_id}")
    tracking = data.get("suivi_vol", {}).get("simvars", [])
    if not isinstance(tracking, list) or any(not isinstance(item, list) or len(item) != 2
                                             or not all(isinstance(part, str) and part for part in item)
                                             for item in tracking):
        raise ProfileDataError(f"Variables de suivi de vol invalides : {profile_id}")


def _validate_map(data: dict[str, Any], profile_id: str) -> None:
    views, controls = data.get("vues"), data.get("commandes")
    if not isinstance(views, dict) or not isinstance(controls, dict):
        raise ProfileDataError(f"Carte invalide : {profile_id}")
    for name, view in views.items():
        if not isinstance(view, dict):
            raise ProfileDataError(f"Vue invalide : {name}")
        for field, size in (("position", 3), ("pbh", 3)):
            values = view.get(field)
            if not isinstance(values, list) or len(values) != size:
                raise ProfileDataError(f"Vue invalide : {name}.{field}")
            for index, value in enumerate(values):
                _finite(value, f"{name}.{field}[{index}]")
        if not 0.05 <= _finite(view.get("fov"), f"{name}.fov") <= 3.2:
            raise ProfileDataError(f"Champ de vision invalide : {name}")
        if "profondeur" in view and not 0.1 <= _finite(view["profondeur"], f"{name}.profondeur") <= 3.0:
            raise ProfileDataError(f"Profondeur invalide : {name}")
    for key, control in controls.items():
        if not isinstance(control, dict) or control.get("vue") not in views:
            raise ProfileDataError(f"Commande sans vue valide : {key}")
        for field in ("u", "v"):
            if not 0.0 <= _finite(control.get(field), f"{key}.{field}") <= 1.0:
                raise ProfileDataError(f"Coordonnée hors écran : {key}.{field}")
        if "profondeur" in control and not 0.1 <= _finite(control["profondeur"], f"{key}.profondeur") <= 3.0:
            raise ProfileDataError(f"Profondeur invalide : {key}")


def _validate_procedure(data: dict[str, Any], profile: "Profile", filename: str) -> None:
    _text(data.get("id"), f"{filename}.id")
    steps = data.get("etapes")
    if not isinstance(steps, list) or not steps:
        raise ProfileDataError(f"Procédure sans étapes : {filename}")
    identifiers = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ProfileDataError(f"Étape invalide : {filename}[{index}]")
        step_id = _text(step.get("id"), f"{filename}[{index}].id")
        if step_id in identifiers:
            raise ProfileDataError(f"Étape dupliquée : {filename}/{step_id}")
        identifiers.add(step_id)
        control = step.get("commande")
        if control is None:
            if not step.get("sans_cible"):
                raise ProfileDataError(f"Commande requise : {filename}/{step_id}")
        else:
            control = _text(control, f"{filename}/{step_id}.commande")
            if control not in profile.carte["commandes"]:
                raise ProfileDataError(f"Commande inconnue : {filename}/{control}")
        expected = step.get("attendu")
        if expected is not None and (not isinstance(expected, dict) or not expected):
            raise ProfileDataError(f"Condition invalide : {filename}/{step_id}")


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
        data = _read_json(path) if path.exists() else {"vues": {}, "commandes": {}}
        _validate_map(data, self.id)
        return data

    @cached_property
    def source(self):
        from flowxav.state import make_source
        return make_source(self.data["etat"], self.folder, self)

    def view(self, name: str) -> Pose:
        return Pose.from_dict(self.carte["vues"][name])

    def view_depth(self, name: str) -> float:
        """Distance (m) de l'œil de la vue au plan du panneau, le long de l'axe de visée."""
        return float(self.carte["vues"][name].get("profondeur", DEFAULT_DEPTH_M))

    def control_depth(self, key: str) -> float:
        """Profondeur de la commande mesurée par stéréo (tools/cartographie/profondeurs.py), sinon celle de sa vue."""
        control = self.carte["commandes"][key]
        return float(control.get("profondeur", self.view_depth(control["vue"])))

    def control(self, key: str) -> Control:
        c = self.carte["commandes"][key]
        return Control(key, c["vue"], c["u"], c["v"], c.get("libelle", key))

    def matches(self, title: str) -> bool:
        lowered = title.lower()
        rule = self.data.get("reconnaissance", {})
        return all(word.lower() in lowered for word in rule.get("titre_contient", [])) and bool(rule)

    def procedures(self) -> list[dict[str, Any]]:
        folder = self.folder / "procedures"
        procedures = []
        identifiers = set()
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            procedure = _read_json(path)
            _validate_procedure(procedure, self, path.name)
            if procedure["id"] in identifiers:
                raise ProfileDataError(f"Procédure dupliquée : {procedure['id']}")
            identifiers.add(procedure["id"])
            procedures.append(procedure)
        return procedures

    def procedure(self, procedure_id: str) -> dict[str, Any]:
        for procedure in self.procedures():
            if procedure["id"] == procedure_id:
                return procedure
        raise KeyError(procedure_id)

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "nom": self.name, "constructeur": self.manufacturer,
                "commandes": len(self.carte["commandes"]), "vues": len(self.carte["vues"]),
                "procedures": [{"id": p["id"], "titre": p["titre"], "etapes": len(p["etapes"]),
                                "description": p.get("description", {}), "demonstration": bool(p.get("demonstration")),
                                "phase": p.get("phase"), "variante": p.get("variante", {})}
                               for p in self.procedures()],
                "depart_etats": self.data.get("depart", {}).get("etats", [])}


def list_profiles() -> list[Profile]:
    profiles = []
    for path in sorted(aircraft_root().glob("*/profile.json")):
        data = _read_json(path)
        _validate_profile(data, path.parent.name)
        profiles.append(Profile(path.parent.name, path.parent, data))
    return profiles


def load_profile(profile_id: str) -> Profile:
    for profile in list_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Profil inconnu : {profile_id}")


def detect(title: str) -> Profile | None:
    """Premier profil dont la règle de reconnaissance correspond au titre de l'avion chargé."""
    return next((profile for profile in list_profiles() if profile.matches(title)), None)
