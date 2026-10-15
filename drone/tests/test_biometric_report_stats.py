from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.config import APP_CONFIG
from drone.data.biometric_report_stats import (
    MAX_GAP_SEC,
    NOMINAL_STEP_SEC,
    alarm_cause_label,
    format_duration,
    monitored_duration_sec,
    relative_time,
    sample_hr_out_of_band,
    sample_spo2_out_of_band,
    sample_step_sec,
    session_clock,
    state_durations,
    state_label,
    telemetry_gaps,
    valid_reading_percent,
)

from biometric_log_samples import logger_con_sessione

_SOGLIE = APP_CONFIG.smartwatch_thresholds


def _campioni():
    return logger_con_sessione().samples()


def test_the_measured_step_is_the_median_and_ignores_the_gap():
    # quaranta campioni ogni mezzo secondo, con dieci secondi di silenzio in
    # mezzo: la cadenza resta quella dell'orologio
    assert abs(sample_step_sec(_campioni()) - 0.5) < 1e-9


def test_a_single_sample_falls_back_to_the_nominal_step():
    assert sample_step_sec([{"timestamp": 1000.0}]) == NOMINAL_STEP_SEC
    assert sample_step_sec([]) == NOMINAL_STEP_SEC


def test_only_the_silences_longer_than_the_limit_count_as_gaps():
    buchi = telemetry_gaps(_campioni())
    assert len(buchi) == 1
    assert buchi[0]["duration"] > MAX_GAP_SEC
    assert abs(buchi[0]["duration"] - 10.5) < 1e-9


def test_the_covered_telemetry_leaves_the_gap_out():
    campioni = _campioni()
    # quaranta campioni da mezzo secondo l'uno
    assert abs(monitored_duration_sec(campioni) - 20.0) < 1e-9


def test_the_state_durations_are_in_the_order_of_the_firmware():
    durate = state_durations(_campioni())
    assert [codice for codice, _durata in durate] == [
        "RICERCA_SEGNALE", "NORMALE", "ALLARME",
    ]
    assert abs(sum(durata for _codice, durata in durate) - 20.0) < 1e-9


def test_an_unknown_state_keeps_its_own_code():
    assert state_label("NORMALE") == "Normale"
    assert state_label("BOH") == "BOH"
    assert state_label(None) == ""


def test_the_valid_readings_are_counted_on_every_sample():
    # i primi quattro campioni su quaranta arrivano senza lettura
    assert abs(valid_reading_percent(_campioni()) - 90.0) < 1e-9
    assert valid_reading_percent([]) is None


def test_the_alarm_cause_comes_from_the_values_of_the_event():
    assert alarm_cause_label({"bpm": 131.0, "spo2": 97.0}, _SOGLIE) == "Battito"
    assert alarm_cause_label({"bpm": 80.0, "spo2": 90.0}, _SOGLIE) == "Saturazione"
    assert alarm_cause_label({"bpm": 130.0, "spo2": 90.0}, _SOGLIE) == "Battito e saturazione"
    assert "Non ricavabile" in alarm_cause_label({"bpm": None, "spo2": None}, _SOGLIE)


def test_a_sample_is_out_of_band_on_the_rounded_value_like_the_firmware():
    # il firmware confronta il battito arrotondato all'intero: 120,4 bpm
    # diventano 120, dentro la soglia massima di ingresso
    assert not sample_hr_out_of_band({"bpm": 120.0, "hr_filtrato": 120.4}, _SOGLIE)
    assert sample_hr_out_of_band({"bpm": 121.0, "hr_filtrato": 120.6}, _SOGLIE)
    assert not sample_hr_out_of_band({"bpm": 50.0, "hr_filtrato": 49.6}, _SOGLIE)
    assert not sample_spo2_out_of_band({"spo2": 92.0, "spo2_filtrato": 91.6}, _SOGLIE)
    assert sample_spo2_out_of_band({"spo2": 91.0, "spo2_filtrato": 91.4}, _SOGLIE)
    assert not sample_hr_out_of_band({"bpm": None, "hr_filtrato": None}, _SOGLIE)


def test_the_session_clock_starts_from_the_first_of_all_the_entries():
    log = logger_con_sessione()
    campioni = log.samples()
    eventi = log.events()
    clock = session_clock(campioni, eventi)
    assert relative_time(campioni[0], clock) == 0.0
    # l'allarme cade dopo venticinque campioni e i dieci secondi di silenzio
    assert abs(relative_time(eventi[0], clock) - (25 * 0.5 + 10.0)) < 1e-9


def test_the_duration_is_written_in_minutes_only_when_it_is_worth_it():
    assert format_duration(0) == "0 s"
    assert format_duration(45.4) == "45 s"
    assert format_duration(60) == "1 min"
    assert format_duration(930) == "15 min 30 s"


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
