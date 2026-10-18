from __future__ import annotations

import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.config import APP_CONFIG
from drone.loaders.waypoint_path_loader import (
    load_waypoint_paths,
    required_model_names,
)


def _write(directory, filename, payload):
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh)
    return path


def _load_collecting_errors(directory):
    logger = logging.getLogger("drone.loaders.waypoint_path_loader")
    messages: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                messages.append(record.getMessage())

    handler = _Collect()
    logger.addHandler(handler)
    try:
        paths = load_waypoint_paths(directory)
    finally:
        logger.removeHandler(handler)
    return paths, messages


def test_load_valid_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "name": "Giro A",
            "description": "desc",
            "waypoints": [
                {"x": 0, "y": 1, "z": 2, "yaw_deg": 90},
                {"x": 1, "y": 2, "z": 2},
            ],
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        p = paths[0]
        assert p.name == "Giro A"
        assert p.description == "desc"
        assert len(p.waypoints) == 2
        assert p.waypoints[1].yaw_deg == 0.0
        assert p.waypoints[0].x == 0 and p.waypoints[0].z == 2
        assert p.supervision_waypoints is None


def test_supervision_waypoints_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
                {"x": 1.5, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [3, 1, 1],
            "supervision_stop_sec": 5,
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].supervision_waypoints == (1, 3)


def test_supervision_empty_list_disables_supervision():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [],
        })
        paths = load_waypoint_paths(d)
        assert paths[0].supervision_waypoints == ()


def test_supervision_invalid_entries_skipped_not_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [0, 5, 2, "x", True, 1.5],
            "supervision_stop_sec": 5,
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].supervision_waypoints == (2,)


def test_supervision_non_list_is_ignored():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": 3,
        })
        paths = load_waypoint_paths(d)
        assert paths[0].supervision_waypoints is None


def test_safety_net_tags_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [1, 2],
            "supervision_stop_sec": 5,
            "safety_net_confirm_sec": 2,
            "safety_net_tags_by_stop": {"1": [3, 15], "2": [4, 5, 13]},
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        mapping = paths[0].safety_net_tags_by_stop
        assert mapping == {1: frozenset({3, 15}), 2: frozenset({4, 5, 13})}
        assert all(isinstance(k, int) for k in mapping)


def test_safety_net_tags_dropped_when_not_supervision_waypoint():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
                {"x": 1.5, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [2],
            "supervision_stop_sec": 5,
            "safety_net_confirm_sec": 2,
            "safety_net_tags_by_stop": {"1": [3, 15], "2": [4, 5]},
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].safety_net_tags_by_stop == {2: frozenset({4, 5})}


def test_safety_net_tags_without_supervision_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
            ],
            "safety_net_tags_by_stop": {"1": [3, 15]},
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("safety_net_tags_by_stop" in m for m in messages), messages


def test_safety_net_tags_unreachable_with_min_sec_names_the_real_cause():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": 5,
            "safety_net_tags_by_stop": {"2": [3]},
            "safety_net_confirm_sec": 1,
        })
        paths, messages = _load_collecting_errors(d)

        assert paths == []
        assert any("safety_net_tags_by_stop" in m for m in messages), messages
        assert not any("'safety_net_confirm_sec' è definito" in m for m in messages), messages


def test_safety_net_tags_absent_is_none():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert paths[0].safety_net_tags_by_stop is None


def test_safety_net_tags_non_dict_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "safety_net_tags_by_stop": [3, 15],
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("safety_net_tags_by_stop" in m for m in messages), messages


def test_safety_net_tags_invalid_entries_skipped_not_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [1],
            "supervision_stop_sec": 5,
            "safety_net_confirm_sec": 2,
            "safety_net_tags_by_stop": {
                "1": [3, "x", True, -2, 15],
                "5": [9],
                "due": [4],
                "2": "non_una_lista",
            },
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].safety_net_tags_by_stop == {1: frozenset({3, 15})}


def test_safety_net_tags_all_invalid_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "safety_net_tags_by_stop": {"1": [], "9": [3]},
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("safety_net_tags_by_stop" in m for m in messages), messages


def test_waypoint_below_safe_altitude_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "bassa.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1.2},
                {"x": 1, "y": 0, "z": 0.05},
            ],
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("quota di sicurezza" in m for m in messages), messages


def test_waypoint_below_the_floor_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "sottoterra.json", {
            "waypoints": [{"x": 0, "y": 0, "z": -0.4}],
        })
        paths, _messages = _load_collecting_errors(d)
        assert paths == []


def test_waypoint_at_the_safe_altitude_is_accepted():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "limite.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 0.5}],
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1


def test_waypoint_outside_the_flight_area_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "refuso.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 0, "y": -45, "z": 1},
            ],
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("fuori dall'area di volo" in m for m in messages), messages


def test_waypoint_too_close_to_a_wall_drops_the_path():
    x_wall = APP_CONFIG.flight_area_x_m[1]
    margin = APP_CONFIG.flight_area_wall_margin_m
    with tempfile.TemporaryDirectory() as d:
        _write(d, "muro.json", {
            "waypoints": [{"x": x_wall - margin / 2, "y": 0, "z": 1}],
        })
        paths, _messages = _load_collecting_errors(d)
        assert paths == []


def test_home_outside_the_flight_area_drops_the_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "home.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "home_waypoint": {"x": 10, "y": 0, "z": 1},
        })
        paths, messages = _load_collecting_errors(d)
        assert paths == []
        assert any("home_waypoint" in m for m in messages), messages


def test_the_project_scenarios_are_inside_the_flight_area():
    paths, messages = _load_collecting_errors(APP_CONFIG.waypoint_paths_dir)
    assert messages == []
    assert len(paths) == len(list(APP_CONFIG.waypoint_paths_dir.glob("*.json")))


def test_supervision_stop_sec_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [
                {"x": 0, "y": 0, "z": 1},
                {"x": 1, "y": 0, "z": 1},
                {"x": 1.5, "y": 0, "z": 1},
            ],
            "supervision_waypoints": [1, 3],
            "supervision_stop_sec": 8,
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].supervision_stop_sec == 8.0


def test_supervision_without_waypoints_has_no_duration():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].supervision_waypoints is None
        assert paths[0].supervision_stop_sec is None


def test_supervision_waypoints_without_duration_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_supervision_duration_without_supervision_waypoints_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_stop_sec": 5,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_supervision_stop_sec_non_numeric_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": {"1": 5},
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_supervision_non_positive_duration_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": 0,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_supervision_invalid_duration_value_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": "tanto",
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_supervision_stop_sec_over_waypoint_timeout_rejected():
    from drone.config import APP_CONFIG

    timeout = APP_CONFIG.apriltag_autopilot.waypoint_timeout_sec
    assert APP_CONFIG.apriltag_autopilot.waypoint_timeout_enabled, (
        "Il test presuppone il timeout di waypoint attivo in config."
    )
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": timeout + 10,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_name_falls_back_to_filename():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "senza_nome.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].name == "senza_nome"


def test_invalid_json_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "rotto.json", "{ questo non e' json valido ")
        _write(d, "ok.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert [p.name for p in paths] == ["ok"]


def test_missing_waypoints_key_skipped():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "senza.json", {"name": "Y"})
        paths = load_waypoint_paths(d)
        assert paths == []


def test_empty_waypoints_skipped():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "vuoto.json", {
            "name": "Senza rotta",
            "description": "Nessun waypoint",
            "waypoints": [],
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_malformed_waypoint_skips_whole_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "bad.json", {"waypoints": [{"x": 0, "y": 0}]})
        paths = load_waypoint_paths(d)
        assert paths == []


def test_non_finite_coordinate_skips_whole_path():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "nan.json", {"waypoints": [{"x": 0, "y": "nan", "z": 1}]})
        _write(d, "inf.json", {"waypoints": [{"x": "inf", "y": 0, "z": 1}]})
        _write(d, "ok.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert [p.name for p in paths] == ["ok"]


def test_paths_sorted_by_filename():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "02_b.json", {"name": "B", "waypoints": [{"x": 0, "y": 0, "z": 1}]})
        _write(d, "01_a.json", {"name": "A", "waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert [p.name for p in paths] == ["A", "B"]


def test_missing_directory_returns_empty():
    paths = load_waypoint_paths(os.path.join(tempfile.gettempdir(), "non_esiste_xyz_123"))
    assert paths == []


def _safety_net_payload(duration):
    payload = {
        "waypoints": [
            {"x": 0, "y": 0, "z": 1},
            {"x": 1, "y": 0, "z": 1},
        ],
        "supervision_waypoints": [1],
        "supervision_stop_sec": 5,
        "safety_net_tags_by_stop": {"1": [3, 15]},
    }
    if duration is not None:
        payload["safety_net_confirm_sec"] = duration
    return payload


def test_safety_net_confirm_sec_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _safety_net_payload(2))
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].safety_net_confirm_sec == 2.0


def test_safety_net_confirm_zero_allowed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _safety_net_payload(0))
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].safety_net_confirm_sec == 0.0


def test_safety_net_confirm_required():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _safety_net_payload(None))
        paths = load_waypoint_paths(d)
        assert paths == []


def test_safety_net_confirm_without_tags_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "safety_net_confirm_sec": 2,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_safety_net_confirm_not_below_hold_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _safety_net_payload(5))
        paths = load_waypoint_paths(d)
        assert paths == []


def test_safety_net_confirm_negative_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _safety_net_payload(-1))
        paths = load_waypoint_paths(d)
        assert paths == []


def test_home_waypoint_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "home_waypoint": {"x": 0, "y": 0, "z": 2, "yaw_deg": 90},
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        home = paths[0].home_waypoint
        assert home is not None
        assert (home.x, home.y, home.z, home.yaw_deg) == (0.0, 0.0, 2.0, 90.0)


def test_home_waypoint_absent_is_none():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert paths[0].home_waypoint is None


def test_home_waypoint_unsafe_z_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "home_waypoint": {"x": 0, "y": 0, "z": 0.3},
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_home_waypoint_malformed_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "home_waypoint": {"x": 0, "y": 0},
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_surveillance_thresholds_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "fall_alarm_after_sec": 0.5,
            "restricted_area_alarm_after_sec": 0.8,
            "restricted_area_tolerance_px": 30,
        })
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].fall_alarm_after_sec == 0.5
        assert paths[0].restricted_area_alarm_after_sec == 0.8
        assert paths[0].restricted_area_tolerance_px == 30.0


def test_surveillance_thresholds_absent_are_none():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {"waypoints": [{"x": 0, "y": 0, "z": 1}]})
        paths = load_waypoint_paths(d)
        assert paths[0].fall_alarm_after_sec is None
        assert paths[0].restricted_area_alarm_after_sec is None
        assert paths[0].restricted_area_tolerance_px is None


def test_restricted_area_alarm_after_sec_negative_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "restricted_area_alarm_after_sec": -1,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_restricted_area_tolerance_px_zero_allowed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "restricted_area_tolerance_px": 0,
        })
        paths = load_waypoint_paths(d)
        assert paths[0].restricted_area_tolerance_px == 0.0


def test_negative_threshold_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "restricted_area_tolerance_px": -5,
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_non_numeric_threshold_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "fall_alarm_after_sec": "presto",
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def _dpi_scenario(**extra):
    payload = {
        "waypoints": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 1}],
        "supervision_waypoints": [1],
        "supervision_stop_sec": 10,
    }
    payload.update(extra)
    return payload


def test_dpi_parsed():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario(
            dpi_required=["helmet", "goggles", "vest", "shoes"],
            dpi_alarm_after_sec=3,
        ))
        paths = load_waypoint_paths(d)
        assert len(paths) == 1
        assert paths[0].dpi_required == ("goggles", "helmet", "shoes", "vest")
        assert paths[0].dpi_alarm_after_sec == 3.0


def test_dpi_absent_is_none():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario())
        paths = load_waypoint_paths(d)
        assert paths[0].dpi_required is None
        assert paths[0].dpi_alarm_after_sec is None


def test_dpi_non_string_items_dropped():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario(
            dpi_required=["helmet", 5, True, ""],
            dpi_alarm_after_sec=2,
        ))
        paths = load_waypoint_paths(d)
        assert paths[0].dpi_required == ("helmet",)


def test_dpi_without_supervision_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "dpi_required": ["helmet"],
        })
        paths = load_waypoint_paths(d)
        assert paths == []


def test_dpi_missing_duration_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario(dpi_required=["helmet"]))
        paths = load_waypoint_paths(d)
        assert paths == []


def test_dpi_duration_not_less_than_hold_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario(
            dpi_required=["helmet"], dpi_alarm_after_sec=10,
        ))
        paths = load_waypoint_paths(d)
        assert paths == []


def test_dpi_duration_without_control_rejected():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.json", _dpi_scenario(dpi_alarm_after_sec=2))
        paths = load_waypoint_paths(d)
        assert paths == []


def _avvisi_del_loader(azione) -> list[str]:
    registro: list[str] = []

    class _Cattura(logging.Handler):
        def emit(self, record):
            registro.append(record.getMessage())

    logger = logging.getLogger("drone.loaders.waypoint_path_loader")
    handler = _Cattura()
    logger.addHandler(handler)
    livello = logger.level
    logger.setLevel(logging.WARNING)
    try:
        azione()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(livello)
    return registro


def test_the_old_hazard_key_is_ignored_and_reported():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "vecchio.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "hazard_min_consecutive_sec": 5,
        })
        raccolti = []
        avvisi = _avvisi_del_loader(lambda: raccolti.extend(load_waypoint_paths(d)))

    assert len(raccolti) == 1
    assert raccolti[0].fall_alarm_after_sec is None
    assert any("fall_alarm_after_sec" in m and "vecchio.json" in m for m in avvisi)


def test_the_new_fall_key_does_not_warn():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "nuovo.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "fall_alarm_after_sec": 5,
        })
        raccolti = []
        avvisi = _avvisi_del_loader(lambda: raccolti.extend(load_waypoint_paths(d)))

    assert raccolti[0].fall_alarm_after_sec == 5.0
    assert not [m for m in avvisi if "hazard" in m]


_RENAMED = (
    ("dpc_tags", "safety_net_tags_by_stop"),
    ("dpc_min_consecutive_sec", "safety_net_confirm_sec"),
    ("fall_min_consecutive_sec", "fall_alarm_after_sec"),
    ("restricted_area_min_consecutive_sec", "restricted_area_alarm_after_sec"),
    ("restricted_area_proximity_px", "restricted_area_tolerance_px"),
    ("dpi_min_consecutive_sec", "dpi_alarm_after_sec"),
)


def test_the_renamed_keys_are_ignored_and_reported():
    with tempfile.TemporaryDirectory() as d:
        payload = {"waypoints": [{"x": 0, "y": 0, "z": 1}]}
        payload.update({old: 1 for old, _ in _RENAMED})
        payload["dpc_tags"] = {"1": [3]}
        _write(d, "vecchio.json", payload)
        raccolti = []
        avvisi = _avvisi_del_loader(lambda: raccolti.extend(load_waypoint_paths(d)))

    assert len(raccolti) == 1
    percorso = raccolti[0]
    for _, new in _RENAMED:
        assert getattr(percorso, new) is None
    for old, new in _RENAMED:
        assert any(old in m and new in m and "vecchio.json" in m for m in avvisi)


def test_the_old_supervision_key_does_not_silently_disable_the_stops():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "vecchio.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_hold_sec": 8,
        })
        raccolti = []
        avvisi = _avvisi_del_loader(lambda: raccolti.extend(load_waypoint_paths(d)))

    assert raccolti == []
    assert any("supervision_stop_sec" in m for m in avvisi)


def test_the_new_keys_do_not_warn():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "nuovo.json", {
            "waypoints": [{"x": 0, "y": 0, "z": 1}],
            "supervision_waypoints": [1],
            "supervision_stop_sec": 8,
            "safety_net_tags_by_stop": {"1": [3]},
            "safety_net_confirm_sec": 3,
            "restricted_area_tolerance_px": 0,
            "dpi_required": ["helmet"],
            "dpi_alarm_after_sec": 4,
        })
        raccolti = []
        avvisi = _avvisi_del_loader(lambda: raccolti.extend(load_waypoint_paths(d)))

    assert len(raccolti) == 1
    assert avvisi == []


class _Percorso:
    def __init__(self, **campi):
        valori = dict(
            safety_net_tags_by_stop=None,
            restricted_area_tolerance_px=None,
            fall_alarm_after_sec=None,
            dpi_required=None,
        )
        valori.update(campi)
        for chiave, valore in valori.items():
            setattr(self, chiave, valore)


def test_lo_scenario_delle_reti_chiede_solo_il_modello_delle_reti():
    percorso = _Percorso(safety_net_tags_by_stop={2: frozenset({7})})
    assert required_model_names(percorso) == (APP_CONFIG.safety_net_model_name,)


def test_lo_scenario_dell_operatore_chiede_i_tre_modelli():
    percorso = _Percorso(
        restricted_area_tolerance_px=0,
        fall_alarm_after_sec=5,
        dpi_required=("helmet",),
    )
    assert required_model_names(percorso) == (
        APP_CONFIG.person_fall_model_name,
        APP_CONFIG.restricted_area_model_name,
        APP_CONFIG.dpi_model_name,
    )


def test_le_cadute_da_sole_bastano_a_chiedere_il_modello_delle_persone():
    percorso = _Percorso(fall_alarm_after_sec=2.0)
    assert required_model_names(percorso) == (APP_CONFIG.person_fall_model_name,)


def test_uno_scenario_senza_controlli_non_chiede_modelli():
    assert required_model_names(_Percorso()) == ()


def test_nessun_modello_viene_chiesto_due_volte():
    percorso = _Percorso(
        restricted_area_tolerance_px=4, fall_alarm_after_sec=1.0, dpi_required=("vest",),
    )
    nomi = required_model_names(percorso)
    assert len(nomi) == len(set(nomi))


def test_gli_scenari_del_progetto_chiedono_i_modelli_attesi():
    percorsi = {p.source.stem: p for p in load_waypoint_paths(APP_CONFIG.waypoint_paths_dir)}
    collettiva = percorsi.get("1_collective_safety_check")
    operatore = percorsi.get("2_operator_safety_check")
    if collettiva is not None:
        assert required_model_names(collettiva) == (APP_CONFIG.safety_net_model_name,)
    if operatore is not None:
        assert set(required_model_names(operatore)) == {
            APP_CONFIG.person_fall_model_name,
            APP_CONFIG.restricted_area_model_name,
            APP_CONFIG.dpi_model_name,
        }


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
