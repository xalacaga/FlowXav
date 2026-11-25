"""Projection d'un point de la vue de référence dans la vue actuelle de la caméra.

Une fois la caméra rendue, MSFS remet le pilote à sa place (siège du
commandant), alors que les vues de référence sont relevées près de l'axe :
l'œil a bougé. La commande est donc placée dans l'espace, sur le rayon (u, v)
de la vue de référence, là où il coupe le plan du panneau — supposé face à la
vue, à `depth` mètres de son œil le long de l'axe de visée —, puis reprojetée
depuis l'œil actuel. Tant que l'œil ne bouge pas, la profondeur ne compte pas.

Conventions établies (voir Pose) : la POSITION renvoyée par MSFS a x
positif vers la GAUCHE (constaté le 2026-11-25 : œil rendu au siège du
commandant à x = +0,505), y vers le haut, z vers l'avant ; les calculs
ci-dessous travaillent avec x vers la droite (voir _eye) ; tangage en degrés POSITIF vers le bas ; cap positif vers la droite ;
champ de vision VERTICAL en radians (constaté par triangulation de deux vues
du pylône) ; u, v en fraction de la zone client, origine en haut à gauche.
"""

from __future__ import annotations

import math

from flowxav.sim.simconnect import Pose

Vector = tuple[float, float, float]


def _half_extents(fov: float, aspect: float) -> tuple[float, float]:
    ty = math.tan(fov / 2)
    return ty * aspect, ty


def _rotate(vector: Vector, pitch_deg: float, heading_deg: float) -> Vector:
    """Repère caméra → repère avion : tangage autour de x, puis cap autour de y."""
    x, y, z = vector
    p, h = math.radians(pitch_deg), math.radians(heading_deg)
    y, z = y * math.cos(p) - z * math.sin(p), y * math.sin(p) + z * math.cos(p)
    x, z = x * math.cos(h) + z * math.sin(h), -x * math.sin(h) + z * math.cos(h)
    return x, y, z


def _unrotate(vector: Vector, pitch_deg: float, heading_deg: float) -> Vector:
    x, y, z = vector
    p, h = math.radians(pitch_deg), math.radians(heading_deg)
    x, z = x * math.cos(h) - z * math.sin(h), x * math.sin(h) + z * math.cos(h)
    y, z = y * math.cos(p) + z * math.sin(p), -y * math.sin(p) + z * math.cos(p)
    return x, y, z


def direction(pose: Pose, u: float, v: float, aspect: float) -> Vector:
    """Direction (repère avion, normée) du point (u, v) vu depuis `pose`."""
    tx, ty = _half_extents(pose.fov, aspect)
    x, y, z = _rotate(((2 * u - 1) * tx, (1 - 2 * v) * ty, 1.0), pose.pitch, pose.heading)
    norm = math.sqrt(x * x + y * y + z * z)
    return x / norm, y / norm, z / norm


DEFAULT_DEPTH_M = 0.5   # œil de référence → plan du panneau, le long de l'axe de visée (réglé en vol)


def _eye(pose: Pose) -> Vector:
    """Œil dans le repère des calculs (x vers la droite) : MSFS donne x positif vers la gauche."""
    x, y, z = pose.position
    return -x, y, z


def anchor(pose: Pose, u: float, v: float, aspect: float, depth: float = DEFAULT_DEPTH_M) -> Vector:
    """Position de la commande vue en (u, v) depuis `pose`, sur le plan du panneau à `depth` mètres."""
    dx, dy, dz = direction(pose, u, v, aspect)
    ax, ay, az = direction(pose, 0.5, 0.5, aspect)
    along = max(dx * ax + dy * ay + dz * az, 0.2)
    x, y, z = _eye(pose)
    t = depth / along
    return x + dx * t, y + dy * t, z + dz * t


def toward(pose: Pose, point: Vector) -> Vector:
    """Direction du point vu depuis l'œil de `pose`."""
    return tuple(p - e for p, e in zip(point, _eye(pose)))  # type: ignore[return-value]


def project(pose: Pose, vector: Vector, aspect: float) -> tuple[float, float, bool]:
    """(u, v, visible) de la direction dans la vue `pose`.

    Hors champ ou derrière la caméra, (u, v) indique le bord le plus proche dans
    la bonne direction, pour y dessiner une flèche.
    """
    x, y, z = _unrotate(vector, pose.pitch, pose.heading)
    tx, ty = _half_extents(pose.fov, aspect)
    if z <= 1e-6:
        # Derrière : on garde seulement le sens, poussé loin hors champ.
        x, y, z = x or 1e-6, y, 1e-6
    sx, sy = x / (z * tx), y / (z * ty)
    u, v = (sx + 1) / 2, (1 - sy) / 2
    visible = z > 1e-6 and 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0
    if not visible:
        # Ramène sur le bord en conservant la direction depuis le centre.
        scale = max(abs(sx), abs(sy), 1e-9)
        u, v = (sx / scale + 1) / 2, (1 - sy / scale) / 2
    return u, v, visible
