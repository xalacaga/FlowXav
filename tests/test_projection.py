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


def test_point_seen_from_the_captain_seat():
    """MSFS donne x positif vers la GAUCHE : depuis le siège du commandant (x = +0,505,
    constaté en vol), une commande droit devant l'œil central apparaît à DROITE."""
    point = anchor(REF, 0.5, 0.5, ASPECT, depth=0.7)
    assert project(REF, toward(REF, point), ASPECT)[:2] == pytest.approx((0.5, 0.5), abs=1e-9)
    seat = replace(REF, position=(REF.position[0] + 0.5, *REF.position[1:]))
    u, v, visible = project(seat, toward(seat, point), ASPECT)
    assert visible and u > 0.5 and v == pytest.approx(0.5, abs=0.05)


def test_captain_seat_capture_of_the_parking_brake():
    """Capture du 2026-11-25 depuis le siège du commandant : poignée PARKING BRAKE vers (0,65 ; 0,40)."""
    from flowxav.aircraft import load_profile

    profile = load_profile("pmdg_737_900")
    control = profile.control("EVT_CONTROL_STAND_PARK_BRAKE_LEVER")
    reference = profile.view(control.view)
    pose = Pose((0.505, 1.555, 15.402), 61.1, 37.9, 0.0, 0.865)
    point = anchor(reference, control.u, control.v, ASPECT, profile.view_depth(control.view))
    u, v, visible = project(pose, toward(pose, point), ASPECT)
    assert visible and u == pytest.approx(0.651, abs=0.02) and v == pytest.approx(0.398, abs=0.02)


def test_ring_waits_for_the_real_view():
    pose = {"value": None}
    target = Target(0.4, 0.6, "BAT", reference=REF, current=lambda: pose["value"])
    assert target.screen(ASPECT) is None                        # vue pas encore lue : pas d'anneau au hasard
    pose["value"] = REF
    u, v, visible = target.screen(ASPECT)
    assert visible and (u, v) == pytest.approx((0.4, 0.6), abs=1e-9)


def test_freelook_turns_and_zooms_around_the_same_eye():
    from flowxav.sim.freelook import FreeLook

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
