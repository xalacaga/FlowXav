"""Anneau qui suit la commande quand l'utilisateur tourne la vue ou zoome."""

from dataclasses import replace

import pytest

from flowxav.sim.freelook import FreeLook
from flowxav.sim.projection import direction, project
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


def test_freelook_drag_and_zoom():
    look = FreeLook()
    look.reset(0.9)
    look.drag(100, -50, 1080)
    offsets = look.offsets()
    assert offsets.heading > 0 and offsets.pitch < 0          # main à droite et vers le haut
    look.wheel(3)
    assert look.offsets().zoom < 1                              # molette avant : zoom
    look.wheel(-100)
    assert 0.9 * look.offsets().zoom <= 1.6                     # champ de vision borné
    look.reset(0.9)
    assert look.offsets().heading == 0 and look.offsets().zoom == 1
