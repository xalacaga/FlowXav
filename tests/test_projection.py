"""Anneau qui suit la commande pendant que l'utilisateur regarde librement dans MSFS."""

from dataclasses import replace

import pytest

from flowxav.overlay import Target
from flowxav.sim.projection import anchor, direction, project, toward
from flowxav.sim.simconnect import Pose

ASPECT = 1920 / 1080
REF = Pose((0.0, 1.5, 15.0), 40.0, 5.0, 0.0, 0.9)


@pytest.mark.parametrize("u,v", [(0.5, 0.5), (0.1, 0.2), (0.9, 0.85), (0.35, 0.6)])
def test_same_view_gives_same_point(u, v):
    pu, pv, visible = project(REF, direction(REF, u, v, ASPECT), ASPECT)
    assert visible and pu == pytest.approx(u, abs=1e-9) and pv == pytest.approx(v, abs=1e-9)


def test_turning_right_moves_the_control_left():
    vector = direction(REF, 0.5, 0.5, ASPECT)
    u, v, visible = project(replace(REF, heading=REF.heading + 10), vector, ASPECT)
    assert visible and u < 0.5 and v == pytest.approx(0.5, abs=0.02)


def test_looking_down_moves_the_control_up():
    vector = direction(REF, 0.5, 0.5, ASPECT)
    _u, v, visible = project(replace(REF, pitch=REF.pitch + 10), vector, ASPECT)
    assert visible and v < 0.5


def test_zoom_pushes_off_centre_points_outwards():
    vector = direction(REF, 0.7, 0.5, ASPECT)
    u, _v, _ = project(replace(REF, fov=REF.fov * 0.6), vector, ASPECT)
    assert u > 0.7


def test_off_screen_and_behind_are_pinned_to_the_right_edge():
    vector = direction(REF, 0.5, 0.5, ASPECT)
    u, v, visible = project(replace(REF, heading=REF.heading - 60), vector, ASPECT)
    assert not visible and u == pytest.approx(1.0) and 0 <= v <= 1
    _u, _v, visible = project(replace(REF, heading=REF.heading + 180), vector, ASPECT)
    assert not visible


def test_point_seen_from_another_eye():
    """Vue relevée près de l'axe, pilote assis à gauche : la commande reste au même endroit de l'espace."""
    point = anchor(REF, 0.5, 0.5, ASPECT, distance=0.7)
    assert project(REF, toward(REF, point), ASPECT)[:2] == pytest.approx((0.5, 0.5), abs=1e-9)
    seat = replace(REF, position=(REF.position[0] - 0.5, *REF.position[1:]))
    u, v, visible = project(seat, toward(seat, point), ASPECT)
    assert visible and u > 0.5 and v == pytest.approx(0.5, abs=0.05)   # depuis la gauche, elle est à droite


def test_ring_waits_for_the_real_view():
    pose = {"value": None}
    target = Target(0.4, 0.6, "BAT", reference=REF, current=lambda: pose["value"])
    assert target.screen(ASPECT) is None                        # vue pas encore lue : pas d'anneau au hasard
    pose["value"] = REF
    u, v, visible = target.screen(ASPECT)
    assert visible and (u, v) == pytest.approx((0.4, 0.6), abs=1e-9)
