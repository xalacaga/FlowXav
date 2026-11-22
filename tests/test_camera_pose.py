"""L'ordre des angles de la caméra : le piège qui a donné des vues penchées en vol."""

import struct

from flowxav.sim.simconnect import CAMERA_LAYOUT, Pose


def test_layout_is_84_bytes():
    assert CAMERA_LAYOUT.size == 84


def test_get_order_is_pitch_heading_bank():
    raw = CAMERA_LAYOUT.pack(1.0, 2.0, 3.0, 1, 0, 0.0, 0.0, 0.0, -55.0, 12.5, 0.5, 1, 0, 0.9)
    pose = Pose.from_get(CAMERA_LAYOUT.unpack(raw))
    assert (pose.pitch, pose.heading, pose.bank) == (-55.0, 12.5, 0.5)
    assert pose.position == (1.0, 2.0, 3.0)


def test_set_payload_order_is_pitch_bank_heading():
    pose = Pose((0.0, 1.5, 15.0), pitch=-63.0, heading=20.0, bank=0.0, fov=1.18)
    values = CAMERA_LAYOUT.unpack(pose.set_payload())
    assert values[8:11] == (-63.0, 0.0, 20.0)
    assert abs(values[13] - 1.18) < 1e-9


def test_dict_round_trip_keeps_get_order():
    data = {"position": [0.0272, 1.3754, 15.2335], "pbh": [-63.157, -0.634, -0.0], "fov": 1.183}
    pose = Pose.from_dict(data)
    assert pose.heading == -0.634 and pose.bank == 0.0
    assert Pose.from_dict(pose.to_dict()) == pose
    assert struct.calcsize("<3diI3d3fiId") == 84
