from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.surveillance.safety_net_monitor import SafetyNetMonitor
from common_doubles import FakeClock


_SAFETY_NET_MAP = {1: [3, 15], 2: [4, 5, 13], 6: [2, 16], 7: [8, 9, 11]}


def _monitor(tag_map=None):
    return SafetyNetMonitor(tag_map if tag_map is not None else _SAFETY_NET_MAP)


def _timed_monitor(clk, min_sec, tag_map=None):
    return SafetyNetMonitor(
        tag_map if tag_map is not None else _SAFETY_NET_MAP,
        safety_net_confirm_sec=min_sec,
        time_source=clk,
    )


def _hold(monitor, *, target_index, visible_tag_ids, safety_net_detected):
    return monitor.update(
        target_index=target_index,
        supervision_stop_active=True,
        reason="supervision_stop",
        fault=False,
        visible_tag_ids=visible_tag_ids,
        safety_net_detected=safety_net_detected,
    )


def _complete(monitor, *, target_index, visible_tag_ids=(), safety_net_detected=False):
    return monitor.update(
        target_index=target_index,
        supervision_stop_active=False,
        reason="supervision_stop_completed",
        fault=False,
        visible_tag_ids=visible_tag_ids,
        safety_net_detected=safety_net_detected,
    )


def test_present_when_tag_and_safety_net_seen():
    m = _monitor()
    assert _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True) is None
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert verdict["outcome"] == "present"
    assert verdict["waypoint"] == 1
    assert verdict["seen_tags"] == [3]
    assert verdict["tags"] == [3, 15]


def test_missing_when_tag_seen_but_no_safety_net():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={15}, safety_net_detected=False)
    verdict = _complete(m, target_index=0, visible_tag_ids={15}, safety_net_detected=False)
    assert verdict["outcome"] == "missing"
    assert verdict["waypoint"] == 1
    assert verdict["seen_tags"] == [15]


def test_no_tags_when_reference_tag_never_seen():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={7, 99}, safety_net_detected=True)
    verdict = _complete(m, target_index=0, visible_tag_ids={7}, safety_net_detected=True)
    assert verdict["outcome"] == "no_tags"
    assert verdict["seen_tags"] == []


def test_single_tag_is_enough():
    m = _monitor()
    _hold(m, target_index=1, visible_tag_ids={13}, safety_net_detected=True)
    verdict = _complete(m, target_index=1, visible_tag_ids={13}, safety_net_detected=True)
    assert verdict["outcome"] == "present"
    assert verdict["waypoint"] == 2
    assert verdict["seen_tags"] == [13]


def test_observations_accumulate_across_frames():
    m = _monitor()
    assert _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=False) is None
    assert _hold(m, target_index=0, visible_tag_ids=set(), safety_net_detected=True) is None
    verdict = _complete(m, target_index=0, visible_tag_ids=set(), safety_net_detected=False)
    assert verdict["outcome"] == "present"
    assert verdict["seen_tags"] == [3]


def test_multiple_reference_tags_seen_are_all_reported():
    m = _monitor()
    _hold(m, target_index=1, visible_tag_ids={4}, safety_net_detected=True)
    _hold(m, target_index=1, visible_tag_ids={5, 13}, safety_net_detected=True)
    verdict = _complete(m, target_index=1, visible_tag_ids=set(), safety_net_detected=False)
    assert verdict["outcome"] == "present"
    assert verdict["seen_tags"] == [4, 5, 13]


def test_non_monitored_waypoint_never_emits():
    m = _monitor()
    assert _hold(m, target_index=2, visible_tag_ids={3, 15}, safety_net_detected=True) is None
    assert _complete(m, target_index=2, visible_tag_ids={3, 15}, safety_net_detected=True) is None


def test_fault_during_hold_discards_episode():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=False)
    discarded = m.update(
        target_index=0,
        supervision_stop_active=False,
        reason="waypoint_timeout",
        fault=True,
        visible_tag_ids={3},
        safety_net_detected=False,
    )
    assert discarded is None
    assert _complete(m, target_index=0) is None


def test_reset_clears_open_episode():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    m.reset()
    assert _complete(m, target_index=0) is None


def test_transient_non_hold_frame_keeps_episode():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    keep = m.update(
        target_index=0,
        supervision_stop_active=False,
        reason="pose_missing",
        fault=False,
        visible_tag_ids=set(),
        safety_net_detected=False,
    )
    assert keep is None
    _hold(m, target_index=0, visible_tag_ids=set(), safety_net_detected=False)
    verdict = _complete(m, target_index=0)
    assert verdict["outcome"] == "present"
    assert verdict["seen_tags"] == [3]


def test_new_supervision_waypoint_starts_fresh_episode():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    _hold(m, target_index=1, visible_tag_ids={4}, safety_net_detected=False)
    verdict = _complete(m, target_index=1, visible_tag_ids={4}, safety_net_detected=False)
    assert verdict["waypoint"] == 2
    assert verdict["outcome"] == "missing"
    assert verdict["seen_tags"] == [4]


def test_consecutive_episodes_are_independent():
    m = _monitor()
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    first = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert first["outcome"] == "present"
    _hold(m, target_index=5, visible_tag_ids={16}, safety_net_detected=False)
    second = _complete(m, target_index=5, visible_tag_ids={16}, safety_net_detected=False)
    assert second["waypoint"] == 6
    assert second["outcome"] == "missing"
    assert second["seen_tags"] == [16]


def test_waypoints_without_tags_are_not_monitored():
    for tag_map in ({}, {1: []}):
        m = SafetyNetMonitor(tag_map)
        assert _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True) is None
        assert _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True) is None


def test_safety_net_model_name_default_and_override():
    assert _monitor().safety_net_model_name == "collective_protection"
    assert SafetyNetMonitor(_SAFETY_NET_MAP, safety_net_model_name="Altro").safety_net_model_name == "Altro"


def test_present_requires_consecutive_detection_time():
    clk = FakeClock()
    m = _timed_monitor(clk, 2.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.5)
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert verdict["outcome"] == "present"
    assert verdict["seen_tags"] == [3]


def test_missing_when_detection_too_brief():
    clk = FakeClock()
    m = _timed_monitor(clk, 2.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.0)
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert verdict["outcome"] == "missing"
    assert verdict["seen_tags"] == [3]


def test_safety_net_streak_resets_on_frame_without_safety_net():
    clk = FakeClock()
    m = _timed_monitor(clk, 2.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.5)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(0.5)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=False)
    clk.advance(0.5)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.5)
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert verdict["outcome"] == "missing"


def test_safety_net_streak_resets_across_non_hold_gap_frame():
    clk = FakeClock()
    m = _timed_monitor(clk, 2.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.5)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    m.update(
        target_index=0,
        supervision_stop_active=False,
        reason="pose_missing",
        fault=False,
        visible_tag_ids=set(),
        safety_net_detected=False,
    )
    clk.advance(0.5)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    clk.advance(1.5)
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    assert verdict["outcome"] == "missing"


def test_threshold_zero_keeps_single_frame_behavior():
    clk = FakeClock()
    m = _timed_monitor(clk, 0.0)
    _hold(m, target_index=0, visible_tag_ids={3}, safety_net_detected=True)
    verdict = _complete(m, target_index=0, visible_tag_ids={3}, safety_net_detected=False)
    assert verdict["outcome"] == "present"


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
