from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.surveillance.dpi_monitor import DpiMonitor, dpi_item_names
from common_doubles import FakeClock


_DPI_MODEL = "personal_protection"
_BBOX = (10, 10, 60, 120)


def _snapshot(*labels, model=_DPI_MODEL):
    return [
        {
            "name": model,
            "color": (0, 0, 0),
            "detections": [
                {"label": lb, "confidence": 0.9, "bbox": _BBOX} for lb in labels
            ],
        }
    ]


def _monitor(*, required=("helmet", "goggles", "vest", "shoes"),
             alarm_after_sec=0.0, clear_after_sec=0.0, time_source=None):
    return DpiMonitor(
        dpi_model_name=_DPI_MODEL,
        required_items=required,
        alarm_after_sec=alarm_after_sec,
        clear_after_sec=clear_after_sec,
        time_source=time_source,
    )


def _items(alarms):
    return {a["item"] for a in alarms}


def _tipi(alarms):
    return {a["type"] for a in alarms}


def test_missing_helmet_alarm_during_supervision():
    m = _monitor()
    snap = _snapshot("Without_Helmet")
    alarms = m.update(detections_by_model=snap, supervision_active=True)
    assert _items(alarms) == {"helmet"}


def test_not_emitted_outside_supervision():
    m = _monitor()
    snap = _snapshot("Without_Helmet")
    assert m.update(detections_by_model=snap, supervision_active=False) == []


def test_present_dpi_class_is_not_an_alarm():
    m = _monitor()
    snap = _snapshot("Helmet", "Goggles")
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_only_required_items_alarm():
    m = _monitor(required=("helmet",))
    snap = _snapshot("Without_Helmet", "Without_Goggles")
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {"helmet"}


def test_unknown_required_item_is_ignored():
    m = _monitor(required=("helmet", "cappello"))
    assert m.required_items == ("helmet",)
    snap = _snapshot("Without_Helmet")
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {"helmet"}


def test_no_required_items_never_alarms():
    m = _monitor(required=())
    snap = _snapshot("Without_Helmet", "Without_Vest")
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_label_case_insensitive():
    m = _monitor()
    snap = _snapshot("without_helmet")
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {"helmet"}


def test_wrong_model_ignored():
    m = _monitor()
    snap = _snapshot("Without_Helmet", model="person_fall")
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_empty_snapshot_no_alarms():
    m = _monitor()
    assert m.update(detections_by_model=[], supervision_active=True) == []
    assert m.update(detections_by_model=None, supervision_active=True) == []


def test_multiple_missing_items_together():
    m = _monitor()
    snap = _snapshot("Without_Helmet", "Without_Goggles", "Without_Vest", "Without_Safety_shoes")
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {
        "helmet", "goggles", "vest", "shoes",
    }


def test_latches_are_independent():
    m = _monitor()
    helmet = _snapshot("Without_Helmet")
    both = _snapshot("Without_Helmet", "Without_Goggles")
    assert _items(m.update(detections_by_model=helmet, supervision_active=True)) == {"helmet"}
    assert _items(m.update(detections_by_model=both, supervision_active=True)) == {"goggles"}


def test_payload_kind_type_and_title():
    m = _monitor()
    snap = _snapshot("Without_Goggles")
    (alarm,) = m.update(detections_by_model=snap, supervision_active=True)
    assert alarm["type"] == "dpi_missing"
    assert alarm["item"] == "goggles"
    assert "occhiali" in alarm["message"].lower()
    assert alarm["title"]


def test_alarm_not_repeated_while_condition_persists():
    m = _monitor()
    snap = _snapshot("Without_Helmet")
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {"helmet"}
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    assert m.update(detections_by_model=snap, supervision_active=True) == []


def test_alarm_rearms_after_condition_clears():
    m = _monitor()
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}
    # Tutti i DPI rientrati: parte il dpi_ok che spegne l'allarme sull'orologio.
    assert _tipi(m.update(detections_by_model=ok, supervision_active=True)) == {"dpi_ok"}
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}


def test_requires_consecutive_time():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    snap = _snapshot("Without_Helmet")
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    clk.advance(1.0)
    assert m.update(detections_by_model=snap, supervision_active=True) == []
    clk.advance(1.5)
    assert _items(m.update(detections_by_model=snap, supervision_active=True)) == {"helmet"}


def test_streak_resets_on_frame_without_condition():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    m.update(detections_by_model=missing, supervision_active=True)
    clk.advance(1.5)
    m.update(detections_by_model=ok, supervision_active=True)
    clk.advance(0.6)
    assert m.update(detections_by_model=missing, supervision_active=True) == []


def test_release_grace_suppresses_rearm_on_brief_flicker():
    clk = FakeClock()
    m = _monitor(clear_after_sec=0.5, time_source=clk)
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}
    clk.advance(0.1)
    assert m.update(detections_by_model=ok, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=missing, supervision_active=True) == []


def test_release_grace_rearms_after_long_absence():
    clk = FakeClock()
    m = _monitor(clear_after_sec=0.5, time_source=clk)
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}
    clk.advance(0.6)
    assert _tipi(m.update(detections_by_model=ok, supervision_active=True)) == {"dpi_ok"}
    clk.advance(0.1)
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}


def test_alarm_is_not_cleared_when_the_stop_ends():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    missing = _snapshot("Without_Helmet")
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}
    for _ in range(5):
        clk.advance(1.0)
        assert m.update(detections_by_model=[], supervision_active=False) == []


def test_next_stop_sends_dpi_ok_only_after_the_grace():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    missing = _snapshot("Without_Helmet")
    m.update(detections_by_model=missing, supervision_active=True)
    clk.advance(30.0)
    m.update(detections_by_model=[], supervision_active=False)
    assert m.update(detections_by_model=[], supervision_active=True) == []
    clk.advance(1.1)
    assert _tipi(m.update(detections_by_model=[], supervision_active=True)) == {"dpi_ok"}


def test_dpi_still_missing_at_next_stop_is_not_repeated():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    missing = _snapshot("Without_Helmet")
    m.update(detections_by_model=missing, supervision_active=True)
    clk.advance(30.0)
    m.update(detections_by_model=[], supervision_active=False)
    assert m.update(detections_by_model=missing, supervision_active=True) == []


def test_partial_streak_does_not_carry_over_to_the_next_stop():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, time_source=clk)
    missing = _snapshot("Without_Helmet")
    m.update(detections_by_model=missing, supervision_active=True)
    clk.advance(1.5)
    m.update(detections_by_model=missing, supervision_active=True)
    m.update(detections_by_model=[], supervision_active=False)
    clk.advance(10.0)
    assert m.update(detections_by_model=missing, supervision_active=True) == []
    clk.advance(1.0)
    assert m.update(detections_by_model=missing, supervision_active=True) == []


def test_reset_clears_state():
    m = _monitor()
    missing = _snapshot("Without_Helmet")
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}
    m.reset()
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}


def test_sporadic_detections_never_reach_the_threshold():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=2.0, clear_after_sec=0.5, time_source=clk)
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    for _ in range(12):
        assert m.update(detections_by_model=missing, supervision_active=True) == []
        clk.advance(0.4)
        assert m.update(detections_by_model=ok, supervision_active=True) == []
        clk.advance(0.01)


def test_dropped_frame_does_not_restart_the_streak():
    clk = FakeClock()
    m = _monitor(alarm_after_sec=1.0, clear_after_sec=0.5, time_source=clk)
    missing = _snapshot("Without_Helmet")
    ok = _snapshot("Helmet")
    assert m.update(detections_by_model=missing, supervision_active=True) == []
    clk.advance(0.9)
    assert m.update(detections_by_model=missing, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=ok, supervision_active=True) == []
    clk.advance(0.1)
    assert m.update(detections_by_model=missing, supervision_active=True) == []
    clk.advance(0.2)
    assert _items(m.update(detections_by_model=missing, supervision_active=True)) == {"helmet"}


def test_watch_list_is_updated_when_one_dpi_returns():
    clk = FakeClock()
    m = _monitor(clear_after_sec=1.0, time_source=clk)
    both = _snapshot("Without_Helmet", "Without_Vest")
    vest = _snapshot("Without_Vest")
    alarms = m.update(detections_by_model=both, supervision_active=True)
    assert {a["missing"] for a in alarms} == {("elmetto", "gilet")}
    clk.advance(0.5)
    assert m.update(detections_by_model=vest, supervision_active=True) == []
    clk.advance(0.6)
    (update,) = m.update(detections_by_model=vest, supervision_active=True)
    assert update["type"] == "dpi_missing_update"
    assert update["missing"] == ("gilet",)
    clk.advance(0.1)
    assert m.update(detections_by_model=vest, supervision_active=True) == []


def test_new_missing_dpi_carries_the_list_without_a_separate_update():
    m = _monitor()
    helmet = _snapshot("Without_Helmet")
    vest = _snapshot("Without_Vest")
    m.update(detections_by_model=helmet, supervision_active=True)
    alarms = m.update(detections_by_model=vest, supervision_active=True)
    assert _tipi(alarms) == {"dpi_missing"}
    assert alarms[0]["missing"] == ("gilet",)


def test_last_dpi_returning_sends_only_dpi_ok():
    m = _monitor()
    m.update(detections_by_model=_snapshot("Without_Helmet", "Without_Vest"), supervision_active=True)
    assert _tipi(m.update(detections_by_model=[], supervision_active=True)) == {"dpi_ok"}


def test_reset_forgets_the_list_sent_to_the_watch():
    m = _monitor()
    both = _snapshot("Without_Helmet", "Without_Vest")
    m.update(detections_by_model=both, supervision_active=True)
    m.reset()
    alarms = m.update(detections_by_model=both, supervision_active=True)
    assert _items(alarms) == {"helmet", "vest"}
    assert {a["missing"] for a in alarms} == {("elmetto", "gilet")}


def test_item_keys_are_the_four_dpi():
    assert set(dpi_item_names()) == {"elmetto", "occhiali", "gilet", "scarpe"}


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
