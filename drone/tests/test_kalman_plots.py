from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flight_log_samples import logger_with_comparison_data


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


def test_i_quattro_grafici_del_filtro_nascono_tutti():
    if not _matplotlib_available():
        return
    from drone.ui.plots.kalman_plots import save_kalman_plots
    log = logger_with_comparison_data()
    with tempfile.TemporaryDirectory() as d:
        salvati = save_kalman_plots(log, Path(d), _plt())
        nomi = {p.name for p in Path(d).glob("*.png")}
    assert len(salvati) == 4
    assert nomi == {
        "kalman_raw_and_filtered_trajectory.png",
        "kalman_filter_correction.png",
        "kalman_raw_and_filtered_yaw.png",
        "kalman_raw_and_filtered_position.png",
    }


def test_i_dati_del_filtro_tengono_grezzo_e_filtrato_appaiati():
    if not _matplotlib_available():
        return
    from drone.ui.plots.kalman_plots import _prepare
    log = logger_with_comparison_data()
    with tempfile.TemporaryDirectory() as d:
        dati = _prepare(log, Path(d), _plt(), [])
    assert dati.t[0] == 0.0
    for serie in (dati.raw_x, dati.raw_y, dati.filtered_x, dati.filtered_y,
                  dati.raw_yaw, dati.filtered_yaw, dati.error_norm,
                  dati.outlier_rejected):
        assert len(serie) == len(dati.t)
    assert dati.outlier_rejected.dtype == bool
    assert "campioni" in dati.meta


def test_gli_angoli_tornano_fra_meno_180_e_180():
    if not _matplotlib_available():
        return
    import numpy as np
    from drone.ui.plots.kalman_plots import _wrap_deg
    riportati = _wrap_deg(np.array([0.0, 190.0, 359.0, -200.0, 540.0]))
    assert np.all(riportati >= -180.0)
    assert np.all(riportati < 180.0)
    assert riportati[0] == 0.0
    assert riportati[1] == -170.0
    assert riportati[2] == -1.0
    assert riportati[3] == 160.0


def test_il_salto_fra_180_e_meno_180_non_diventa_una_riga_verticale():
    if not _matplotlib_available():
        return
    import numpy as np
    from drone.ui.plots.kalman_plots import _break_angle_wrap
    spezzata = _break_angle_wrap(np.array([170.0, 179.0, -179.0, -170.0]))
    assert np.isnan(spezzata[1])
    assert not np.isnan(spezzata[0])
    assert not np.isnan(spezzata[2])
    assert not np.isnan(spezzata[3])


def test_una_serie_di_un_solo_angolo_resta_intatta():
    if not _matplotlib_available():
        return
    import numpy as np
    from drone.ui.plots.kalman_plots import _break_angle_wrap
    sola = np.array([12.0])
    assert np.array_equal(_break_angle_wrap(sola), sola)


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
