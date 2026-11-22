"""Projection d'un point de la vue de référence dans la vue actuelle de la caméra.

Quand l'utilisateur tourne la tête ou zoome autour du même œil, une commande
garde la même DIRECTION dans le repère de l'avion : il suffit de retrouver
cette direction depuis le point mémorisé (u, v) de la vue de référence, puis
de la reprojeter dans la vue actuelle. Aucune profondeur n'est nécessaire.

Conventions établies (voir Pose) : x vers la droite, y vers le haut, z vers
l'avant ; tangage en degrés POSITIF vers le bas ; cap positif vers la droite ;
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
