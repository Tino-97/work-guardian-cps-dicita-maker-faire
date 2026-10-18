from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flight_log_samples import (
    logger_with_autopilot_data,
    logger_with_both,
    logger_with_comparison_data,
    logger_with_pose_loss,
)


def _matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def _nomi(log) -> tuple[list, set]:
    with tempfile.TemporaryDirectory() as d:
        salvati = log.save_plots(d)
        return salvati, {p.name for p in Path(d).glob("*.png")}


def test_i_dati_dell_autopilota_non_tirano_dentro_i_grafici_del_filtro():
    if not _matplotlib_available():
        return
    salvati, nomi = _nomi(logger_with_autopilot_data())
    assert len(salvati) == 6
    assert all(n.startswith("autopilot_") for n in nomi)


def test_i_dati_del_confronto_non_tirano_dentro_i_grafici_dell_autopilota():
    if not _matplotlib_available():
        return
    salvati, nomi = _nomi(logger_with_comparison_data())
    assert len(salvati) == 4
    assert all(n.startswith("kalman_") for n in nomi)


def test_con_entrambe_le_serie_escono_tutti_e_dieci_i_grafici():
    if not _matplotlib_available():
        return
    salvati, nomi = _nomi(logger_with_both())
    assert len(salvati) == 10
    assert len(nomi) == 10


def test_senza_campioni_non_si_disegna_niente_e_non_si_rompe_niente():
    if not _matplotlib_available():
        return
    log = logger_with_autopilot_data()
    log.autopilot_entries.clear()
    log.comparison_entries.clear()
    salvati, nomi = _nomi(log)
    assert salvati == []
    assert nomi == set()


def test_la_cartella_di_uscita_nasce_se_non_ce_ancora():
    if not _matplotlib_available():
        return
    log = logger_with_autopilot_data()
    with tempfile.TemporaryDirectory() as d:
        cartella = Path(d) / "sessione" / "grafici"
        salvati = log.save_plots(cartella)
        assert cartella.is_dir()
        assert len(salvati) == 6


def test_i_campioni_senza_posa_non_cambiano_i_grafici():
    if not _matplotlib_available():
        return

    _salvati, attesi = _nomi(logger_with_autopilot_data())
    _salvati_con_perdita, ottenuti = _nomi(logger_with_pose_loss())

    assert ottenuti == attesi


def test_con_la_sola_perdita_di_posa_non_si_disegna_niente():
    if not _matplotlib_available():
        return

    from drone.data.flight_data_logger import FlightDataLogger

    log = FlightDataLogger()
    for i in range(4):
        log.log_autopilot_step(
            pose_estimate=None,
            command={"reason": "pose_missing", "fault": False},
            target=None,
            timestamp=1000.0 + 0.05 * i,
        )

    salvati, nomi = _nomi(log)
    assert salvati == []
    assert nomi == set()


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
