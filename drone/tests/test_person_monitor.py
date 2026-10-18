from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.surveillance.person_monitor import PersonMonitor, _box_min_distance
from common_doubles import FakeClock


_FALL_MODEL = "person_fall"
_RECT_MODEL = "restricted_areas"

_PERSON = (100, 100, 140, 140)
_RECT_OVERLAP = (130, 110, 200, 150)
_RECT_NEAR_20 = (160, 100, 200, 140)
_RECT_FAR = (300, 300, 360, 360)
_RECT_ABOVE = (100, 60, 140, 100)
_RECT_BELOW_20 = (100, 160, 140, 200)


def _snapshot(*entries):
    return [
        {
            "name": name,
            "color": (0, 0, 0),
            "detections": [
                {"label": label, "confidence": 0.9, "bbox": bbox} for label, bbox in dets
            ],
        }
        for name, dets in entries
    ]


def _monitor(
    *,
    restricted_area_tolerance_px=0.0,
    alarm_after_sec=0.0,
    restricted_area_alarm_after_sec=None,
    fall_alarm_after_sec=None,
    clear_after_sec=0.0,
    time_source=None,
):
    return PersonMonitor(
        fall_model_name=_FALL_MODEL,
        person_model_name=_FALL_MODEL,
        restricted_area_model_name=_RECT_MODEL,
        person_label="Person",
        fall_label="Fall",
        restricted_area_tolerance_px=restricted_area_tolerance_px,
        restricted_area_alarm_after_sec=(
            alarm_after_sec
            if restricted_area_alarm_after_sec is None
            else restricted_area_alarm_after_sec
        ),
        fall_alarm_after_sec=(
            alarm_after_sec
            if fall_alarm_after_sec is None
            else fall_alarm_after_sec
        ),
        clear_after_sec=clear_after_sec,
        time_source=time_source,
    )


def _types(alarms):
    return {a["type"] for a in alarms}


def test_box_min_distance_overlap_is_zero():
    assert _box_min_distance(_PERSON, _RECT_OVERLAP) == 0.0


def test_box_min_distance_axis_gap():
    assert _box_min_distance(_PERSON, _RECT_NEAR_20) == 20.0


def test_box_min_distance_diagonal():
    d = _box_min_distance(_PERSON, _RECT_FAR)
    assert abs(d - (160 * 2 ** 0.5)) < 1e-6


def test_crossing_alarm_on_overlap():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"restricted_area"}


def test_crossing_payload_kind_and_title():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    (alarm,) = m.update(detections_by_model=snap, supervision_active=True)
    assert alarm["type"] == "restricted_area"
    assert "superamento" in alarm["title"].lower()


def test_strict_tolerance_needs_true_overlap():
    m = _monitor(restricted_area_tolerance_px=0.0)
    near = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_NEAR_20)]))
    assert m.update(detections_by_model=near, supervision_active=True) == []


def test_positive_tolerance_accepts_small_gap():
    m = _monitor(restricted_area_tolerance_px=25.0)
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_NEAR_20)]))
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"restricted_area"}


def test_gap_beyond_tolerance_no_alarm():
    m = _monitor(restricted_area_tolerance_px=10.0)
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_NEAR_20)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_crossing_uses_feet_not_whole_body():
    assert _box_min_distance(_PERSON, _RECT_ABOVE) == 0.0
    m = _monitor(restricted_area_tolerance_px=25.0)
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_ABOVE)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_crossing_measures_feet_distance_below():
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_BELOW_20)]))
    assert _types(_monitor(restricted_area_tolerance_px=25.0).update(
        detections_by_model=snap, supervision_active=True)) == {"restricted_area"}
    assert _monitor(restricted_area_tolerance_px=10.0).update(
        detections_by_model=snap, supervision_active=True) == []


def test_crossing_not_emitted_outside_supervision():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert m.update(detections_by_model=snap, supervision_active=False) == []


def test_area_alarm_is_not_cleared_when_the_stop_ends():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    crossing = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert _types(m.update(detections_by_model=crossing, supervision_active=True)) == {"restricted_area"}
    for _ in range(5):
        clk.advance(1.0)
        assert m.update(detections_by_model=[], supervision_active=False) == []


def test_next_stop_clears_the_area_alarm_only_after_the_grace():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    crossing = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    m.update(detections_by_model=crossing, supervision_active=True)
    clk.advance(30.0)
    m.update(detections_by_model=[], supervision_active=False)
    assert m.update(detections_by_model=[], supervision_active=True) == []
    clk.advance(1.1)
    assert _types(m.update(detections_by_model=[], supervision_active=True)) == {"restricted_area_ok"}


def test_area_alarm_still_seen_at_next_stop_is_not_repeated():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    crossing = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    m.update(detections_by_model=crossing, supervision_active=True)
    clk.advance(30.0)
    m.update(detections_by_model=[], supervision_active=False)
    assert m.update(detections_by_model=crossing, supervision_active=True) == []


def test_fall_rearms_at_the_next_stop():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}
    clk.advance(5.0)
    m.update(detections_by_model=[], supervision_active=False)
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


def test_no_crossing_without_rect():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_no_crossing_without_person():
    m = _monitor()
    snap = _snapshot((_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_rect_uses_all_boxes_regardless_of_label():
    m = _monitor()
    snap = _snapshot(
        (_FALL_MODEL, [("Person", _PERSON)]),
        (_RECT_MODEL, [("qualsiasi_nome", _RECT_OVERLAP)]),
    )
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"restricted_area"}


def test_person_label_case_insensitive():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"restricted_area"}


def test_non_person_class_does_not_count_as_person():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    alarms = m.update(detections_by_model=snap, supervision_active=True)
    assert _types(alarms) == {"fall"}


def test_crossing_disabled_when_threshold_none():
    m = _monitor(restricted_area_tolerance_px=None)
    over = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert m.update(detections_by_model=over, supervision_active=True) == []
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


def test_fall_alarm_during_supervision():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"fall"}


def test_fall_not_emitted_outside_supervision():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert m.update(detections_by_model=snap, supervision_active=False) == []


def test_fall_and_crossing_together():
    m = _monitor()
    snap = _snapshot(
        (_FALL_MODEL, [("Person", _PERSON), ("Fall", (50, 50, 90, 90))]),
        (_RECT_MODEL, [("rect", _RECT_OVERLAP)]),
    )
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {
        "restricted_area",
        "fall",
    }


def test_alarm_not_repeated_while_condition_persists():
    m = _monitor()
    snap = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"fall"}
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_alarm_rearms_after_condition_clears():
    m = _monitor()
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}
    assert m.update(detections_by_model=empty, supervision_active=True) == []
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


def test_empty_snapshot_no_alarms():
    m = _monitor()
    assert m.update(detections_by_model=[], supervision_active=True) == []
    assert m.update(detections_by_model=None, supervision_active=True) == []


def test_crossing_requires_consecutive_time():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    snap = _snapshot((_FALL_MODEL, [("Person", _PERSON)]), (_RECT_MODEL, [("rect", _RECT_OVERLAP)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    clk.advance(1.0)
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    clk.advance(1.5)
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"restricted_area"}


def test_fall_requires_consecutive_time():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    snap = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    clk.advance(2.5)
    assert _types(m.update(detections_by_model=snap, supervision_active=True)) == {"fall"}


def test_streak_resets_on_frame_without_condition():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    m.update(detections_by_model=fall, supervision_active=True)
    clk.advance(1.5)
    m.update(detections_by_model=empty, supervision_active=True)
    clk.advance(0.6)
    assert m.update(detections_by_model=fall, supervision_active=True) == []


def test_crossing_and_fall_timers_are_independent():
    clk = FakeClock()
    m = _monitor(
        restricted_area_alarm_after_sec=0.0,
        fall_alarm_after_sec=2.0,
        time_source=clk,
    )
    scene = _snapshot(
        (_FALL_MODEL, [("Person", _PERSON), ("Fall", (50, 50, 90, 90))]),
        (_RECT_MODEL, [("rect", _RECT_OVERLAP)]),
    )
    assert _types(m.update(detections_by_model=scene, supervision_active=True)) == {"restricted_area"}
    clk.advance(2.5)
    assert _types(m.update(detections_by_model=scene, supervision_active=True)) == {"fall"}


def test_release_grace_suppresses_rearm_on_brief_flicker():
    clk = FakeClock()
    m = _monitor(clear_after_sec=0.5, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}
    clk.advance(0.1)
    assert m.update(detections_by_model=empty, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=fall, supervision_active=True) == []


def test_release_grace_rearms_after_long_absence():
    clk = FakeClock()
    m = _monitor(clear_after_sec=0.5, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}
    clk.advance(0.6)
    assert m.update(detections_by_model=empty, supervision_active=True) == []
    clk.advance(0.1)
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


def test_sporadic_detections_never_reach_the_threshold():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, clear_after_sec=0.5, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    for _ in range(12):
        assert m.update(detections_by_model=fall, supervision_active=True) == []
        clk.advance(0.4)
        assert m.update(detections_by_model=empty, supervision_active=True) == []
        clk.advance(0.01)


def test_dropped_frame_does_not_restart_the_streak():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=1.0, clear_after_sec=0.5, time_source=clk)
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    empty = _snapshot()
    assert m.update(detections_by_model=fall, supervision_active=True) == []
    clk.advance(0.9)
    assert m.update(detections_by_model=fall, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=empty, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=fall, supervision_active=True) == []
    clk.advance(0.2)
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


def test_reset_clears_state():
    m = _monitor()
    fall = _snapshot((_FALL_MODEL, [("Fall", _PERSON)]))
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}
    m.reset()
    assert _types(m.update(detections_by_model=fall, supervision_active=True)) == {"fall"}


_RECT_UNDER_FEET = (60, 130, 105, 150)
_SECOND_PERSON = (150, 100, 190, 140)


def test_una_persona_su_due_rettangoli_resta_una_persona():
    monitor = _monitor(restricted_area_tolerance_px=0.0)

    alarms = monitor.update(
        detections_by_model=_snapshot(
            (_FALL_MODEL, [("Person", _PERSON)]),
            (_RECT_MODEL, [
                ("Red Rectangle", _RECT_OVERLAP),
                ("Red Rectangle", _RECT_UNDER_FEET),
            ]),
        ),
        supervision_active=True,
    )

    assert [a["message"] for a in alarms] == ["Una persona in un'area vietata"]


def test_due_persone_nella_stessa_area_sono_piu_di_una():
    monitor = _monitor(restricted_area_tolerance_px=0.0)

    alarms = monitor.update(
        detections_by_model=_snapshot(
            (_FALL_MODEL, [("Person", _PERSON), ("Person", _SECOND_PERSON)]),
            (_RECT_MODEL, [("Red Rectangle", _RECT_OVERLAP)]),
        ),
        supervision_active=True,
    )

    assert [a["message"] for a in alarms] == ["Più persone in un'area vietata"]


def test_l_area_liberata_genera_il_rientro_per_l_orologio():
    monitor = _monitor(restricted_area_tolerance_px=0.0)
    dentro = _snapshot(
        (_FALL_MODEL, [("Person", _PERSON)]),
        (_RECT_MODEL, [("Red Rectangle", _RECT_OVERLAP)]),
    )
    fuori = _snapshot((_RECT_MODEL, [("Red Rectangle", _RECT_OVERLAP)]))

    assert [a["type"] for a in monitor.update(
        detections_by_model=dentro, supervision_active=True
    )] == ["restricted_area"]

    # Senza questo evento l'orologio resterebbe in allarme a area ormai libera.
    assert [a["type"] for a in monitor.update(
        detections_by_model=fuori, supervision_active=True
    )] == ["restricted_area_ok"]

    # Il rientro si annuncia una volta sola, non a ogni fotogramma.
    assert monitor.update(detections_by_model=fuori, supervision_active=True) == []


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
