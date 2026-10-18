from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.config import APP_CONFIG, AutopilotWaypointConfig
from drone.flight.subsystem_builders import _select_yolo_configs, create_apriltag_autopilot


def _sample_waypoints():
    return [
        AutopilotWaypointConfig(x=0.0, y=0.0, z=1.5),
        AutopilotWaypointConfig(x=1.0, y=0.0, z=1.5),
    ]


def test_factory_builds_autopilot_for_valid_path():
    autopilot = create_apriltag_autopilot(_sample_waypoints(), [1], 5.0)
    assert autopilot is not None
    assert len(autopilot.waypoints) == 2
    assert autopilot.home_waypoint_index is None
    assert 0 in autopilot.supervision_waypoint_indices
    assert autopilot.supervision_stop_sec == 5.0


def test_factory_appends_home_from_path():
    home = AutopilotWaypointConfig(x=0.0, y=0.0, z=2.0, yaw_deg=90.0)
    autopilot = create_apriltag_autopilot(_sample_waypoints(), None, None, home)
    assert autopilot is not None
    assert len(autopilot.waypoints) == 3
    assert autopilot.home_waypoint_index == 2


def test_factory_returns_none_without_waypoints():
    assert create_apriltag_autopilot([], None, None) is None
    assert create_apriltag_autopilot(None, None, None) is None


def test_factory_no_supervision_when_path_omits_it():
    autopilot = create_apriltag_autopilot(_sample_waypoints(), None, None)
    assert autopilot is not None
    assert autopilot.supervision_waypoint_indices == ()
    assert autopilot.supervision_stop_sec == 0.0


def test_factory_does_not_crash_on_incoherent_supervision_stop():
    timeout = APP_CONFIG.apriltag_autopilot.waypoint_timeout_sec
    assert APP_CONFIG.apriltag_autopilot.waypoint_timeout_enabled, (
        "Il test presuppone il timeout di waypoint attivo in config."
    )
    bad_hold = float(timeout) + 10.0
    autopilot = create_apriltag_autopilot(_sample_waypoints(), [1], bad_hold)
    assert autopilot is None


def test_select_yolo_configs_none_returns_all_models():
    sel = _select_yolo_configs(None)
    assert len(sel) == len(APP_CONFIG.yolo_models)
    assert {m.name for m in sel} == {m.name for m in APP_CONFIG.yolo_models}


def test_select_yolo_configs_empty_returns_nothing():
    assert _select_yolo_configs([]) == []


def test_select_yolo_configs_filters_by_name():
    sel = _select_yolo_configs(["person_fall"])
    assert [m.name for m in sel] == ["person_fall"]


def test_select_yolo_configs_preserves_user_order():
    sel = _select_yolo_configs(["person_fall", "personal_protection"])
    assert [m.name for m in sel] == ["person_fall", "personal_protection"]


def test_select_yolo_configs_unknown_names_are_skipped():
    sel = _select_yolo_configs(["personal_protection", "Inesistente"])
    assert [m.name for m in sel] == ["personal_protection"]


def _run_all() -> int:
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"[ OK ] {name}")

    print("-" * 60)
    print(f"Totale: {len(tests)}  |  passati: {passed}  |  falliti: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
