from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.data.flight_data_logger import FlightDataLogger
from flight_log_samples import _WP


def _pose(x=0.0, y=0.0, z=1.5, yaw=0.0):
    return {
        "position_world": {"x": x, "y": y, "z": z},
        "yaw_world_deg": yaw,
        "source": "weighted_average",
        "source_tag_ids": [1],
    }


def _cmd():
    return {
        "lr": 0, "fb": 0, "ud": 0, "yaw": 0,
        "distance_xy": 0.1, "distance_3d": 0.12, "yaw_error_deg": 1.0,
        "target_index": 0, "reached": False, "finished": False, "fault": False,
        "reason": "tracking",
        "xy_tolerance_m": 0.15, "z_tolerance_m": 0.20, "yaw_tolerance_deg": 4.0,
    }


def test_cap_limits_comparison_entries():
    log = FlightDataLogger(max_samples=3)
    for i in range(10):
        log.log_pose_pair(raw_pose_estimate=_pose(0.1 * i), filtered_pose_estimate=None)
    assert len(log.comparison_entries) == 3
    assert abs(log.comparison_entries[-1]["raw_x"] - 0.1 * 9) < 1e-3
    assert abs(log.comparison_entries[0]["raw_x"] - 0.1 * 7) < 1e-3


def test_cap_limits_autopilot_entries():
    log = FlightDataLogger(max_samples=2)
    for i in range(5):
        log.log_autopilot_step(
            pose_estimate=_pose(0.1 * i),
            command=_cmd(),
            target=_WP(0.0, 1.35, 1.5, 90.0),
        )
    assert len(log.autopilot_entries) == 2
    assert abs(log.autopilot_entries[-1]["x"] - 0.1 * 4) < 1e-3
    assert abs(log.autopilot_entries[0]["x"] - 0.1 * 3) < 1e-3


def test_ring_buffer_keeps_most_recent():
    log = FlightDataLogger(max_samples=1)
    assert log.log_pose_pair(_pose(x=1.0), None) is True
    assert log.log_pose_pair(_pose(x=2.0), None) is True
    assert len(log.comparison_entries) == 1
    assert log.comparison_entries[-1]["raw_x"] == 2.0


def test_no_cap_when_none():
    log = FlightDataLogger(max_samples=None)
    for i in range(50):
        log.log_pose_pair(raw_pose_estimate=_pose(0.01 * i), filtered_pose_estimate=None)
    assert len(log.comparison_entries) == 50


def test_il_fault_senza_posa_resta_registrato():
    log = FlightDataLogger()

    assert log.log_autopilot_step(
        pose_estimate=None,
        command={
            "lr": 0, "fb": 0, "ud": 0, "yaw": 0,
            "target_index": 1, "reached": False, "finished": False,
            "fault": True, "reason": "pose_timeout",
        },
        target=None,
        timestamp=1000.0,
    ) is True

    entry = log.autopilot_entries[-1]
    assert entry["x"] is None
    assert entry["y"] is None
    assert entry["z"] is None
    assert entry["yaw_deg"] is None
    assert entry["tag_ids"] == []
    assert entry["pose_source"] == ""
    assert entry["fault"] is True
    assert entry["reason"] == "pose_timeout"


def test_senza_comando_non_si_registra_niente():
    log = FlightDataLogger()

    assert log.log_autopilot_step(pose_estimate=None, command=None) is False
    assert len(log.autopilot_entries) == 0


def test_la_sessione_nasce_nella_cartella_data_al_logger():
    with tempfile.TemporaryDirectory() as d:
        log = FlightDataLogger(output_dir=d)
        log.log_pose_pair(_pose(), _pose(0.01))
        log.log_pose_pair(_pose(0.02), _pose(0.03))

        sessione = log.export_session()

        assert sessione is not None
        assert sessione.parent == Path(d)
        assert sessione.name.startswith("flight_session_")
        assert any(sessione.iterdir())


def test_senza_campioni_la_cartella_di_sessione_non_viene_creata():
    with tempfile.TemporaryDirectory() as d:
        log = FlightDataLogger(output_dir=d)
        assert log.export_session() is None
        assert list(Path(d).iterdir()) == []


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
