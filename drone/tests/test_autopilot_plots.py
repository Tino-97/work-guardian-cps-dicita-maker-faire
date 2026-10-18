from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flight_log_samples import logger_with_autopilot_data


def _matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _prepared(log, cartella):
    from drone.ui.plots.autopilot_plots import _prepare
    return _prepare(log, Path(cartella), _plt(), [])


def _senza_tolleranze(log):
    log.autopilot_xy_tolerance_m = None
    log.autopilot_z_tolerance_m = None
    log.autopilot_yaw_tolerance_deg = None
    return log


def test_i_sei_grafici_dell_autopilota_nascono_tutti():
    if not _matplotlib_available():
        return
    from drone.ui.plots.autopilot_plots import save_autopilot_plots
    log = logger_with_autopilot_data()
    with tempfile.TemporaryDirectory() as d:
        salvati = save_autopilot_plots(log, Path(d), _plt())
        nomi = {p.name for p in Path(d).glob("*.png")}
    assert len(salvati) == 6
    assert nomi == {
        "autopilot_flown_trajectory.png",
        "autopilot_waypoint_distance.png",
        "autopilot_rc_commands.png",
        "autopilot_xy_position_error.png",
        "autopilot_altitude_error.png",
        "autopilot_yaw_error.png",
    }


def test_le_tolleranze_vengono_dal_volo_quando_ci_sono():
    if not _matplotlib_available():
        return
    log = logger_with_autopilot_data()
    log.autopilot_xy_tolerance_m = 0.42
    log.autopilot_z_tolerance_m = 0.33
    log.autopilot_yaw_tolerance_deg = 11.0
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    assert (dati.xy_tol, dati.z_tol, dati.yaw_tol) == (0.42, 0.33, 11.0)


def test_senza_tolleranze_registrate_si_ripiega_sulla_configurazione():
    if not _matplotlib_available():
        return
    from drone.config import APP_CONFIG
    log = _senza_tolleranze(logger_with_autopilot_data())
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    autopilota = APP_CONFIG.apriltag_autopilot
    assert dati.xy_tol == autopilota.xy_tolerance_m
    assert dati.z_tol == autopilota.z_tolerance_m
    assert dati.yaw_tol == autopilota.yaw_tolerance_deg


def test_dentro_o_fuori_tolleranza_resta_ignoto_se_il_volo_non_le_ha_registrate():
    if not _matplotlib_available():
        return
    log = _senza_tolleranze(logger_with_autopilot_data())
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    assert dati.within_tol is None


def test_dentro_o_fuori_tolleranza_si_calcola_con_le_tolleranze_del_volo():
    if not _matplotlib_available():
        return
    log = logger_with_autopilot_data()
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    assert dati.within_tol is not None
    assert len(dati.within_tol) == len(dati.t)
    assert dati.within_tol.dtype == bool


def test_i_limiti_di_saturazione_vengono_dalla_configurazione():
    if not _matplotlib_available():
        return
    from drone.config import APP_CONFIG
    log = logger_with_autopilot_data()
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    autopilota = APP_CONFIG.apriltag_autopilot
    assert dati.sat_limits == {
        "fb": int(autopilota.max_xy_speed),
        "lr": int(autopilota.max_xy_speed),
        "ud": int(autopilota.max_z_speed),
        "yaw_cmd": int(autopilota.max_yaw_speed),
    }


def test_il_tempo_parte_da_zero_e_le_etichette_seguono_i_cambi_di_waypoint():
    if not _matplotlib_available():
        return
    log = logger_with_autopilot_data()
    with tempfile.TemporaryDirectory() as d:
        dati = _prepared(log, d)
    assert dati.t[0] == 0.0
    assert len(dati.wp_change_times) == len(dati.wp_change_labels)
    assert all(e.startswith("W") for e in dati.wp_change_labels)
    assert "campioni" in dati.meta


def test_il_lisciamento_lascia_stare_le_serie_troppo_corte():
    if not _matplotlib_available():
        return
    import numpy as np
    from drone.ui.plots.autopilot_plots import _smooth_path
    corta = np.array([1.0, 5.0])
    assert np.array_equal(_smooth_path(corta, 3.0), corta)
    serie = np.array([1.0, 5.0, 1.0, 5.0, 1.0])
    assert np.array_equal(_smooth_path(serie, 0.0), serie)


def test_il_lisciamento_smussa_i_denti_di_sega():
    if not _matplotlib_available():
        return
    import numpy as np
    from drone.ui.plots.autopilot_plots import _smooth_path
    serie = np.array([0.0, 10.0] * 10)
    lisciata = _smooth_path(serie, 3.0)
    assert lisciata.size == serie.size
    assert np.ptp(lisciata) < np.ptp(serie)


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
