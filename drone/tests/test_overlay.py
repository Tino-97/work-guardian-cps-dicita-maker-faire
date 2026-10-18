from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np

from drone.ui.video import overlay
from drone.ui.video.overlay import (
    draw_project_title,
    draw_safety_net_verdict_banner,
    draw_status_overlay,
    draw_watch_overlay,
)
from common_doubles import blank_frame as _frame


def _status(battery=78, connected=True, flying=True, joystick=True):
    return {
        "connected": connected, "joystick": joystick,
        "flying": flying, "battery": battery,
    }


def _row_has_content(frame, y):
    return bool(np.any(frame[y, :, :]))


def _changed(frame):
    return bool(np.any(frame))


class _CountingBuilder:
    def __init__(self):
        self.original = overlay._build_status_panel
        self.calls = 0

    def __enter__(self):
        overlay._STATUS_PANEL_CACHE.clear()

        def counting(*args, **kwargs):
            self.calls += 1
            return self.original(*args, **kwargs)

        overlay._build_status_panel = counting
        return self

    def __exit__(self, *exc):
        overlay._build_status_panel = self.original
        overlay._STATUS_PANEL_CACHE.clear()
        return False


def test_status_overlay_draws_and_returns_none():
    f = _frame()
    assert draw_status_overlay(f, _status(), detection_enabled=True, autonomy_enabled=True) is None
    assert bool(np.any(f))
    assert _row_has_content(f, 30)
    assert _row_has_content(f, 120)


def test_status_overlay_is_compact_no_wide_title_band():
    f = _frame()
    draw_status_overlay(f, _status(), detection_enabled=True, autonomy_enabled=True)
    assert bool(np.any(f[:220, :200, :]))
    assert not bool(np.any(f[:220, 260:, :]))


def _watch(connected=True, bpm=72, spo2=98):
    return {"connected": connected, "bpm": bpm, "spo2": spo2}


def test_watch_overlay_is_drawn_on_the_right_edge():
    f = _frame()
    draw_watch_overlay(f, _watch(connected=False, bpm=None, spo2=None))
    assert bool(np.any(f[:220, overlay.watch_panel_left_edge(f.shape[1]):, :]))
    assert not bool(np.any(f[:220, : overlay.status_panel_right_edge(), :]))


def test_watch_overlay_mirrors_the_drone_panel_margins():
    f = _frame()
    left = overlay.status_panel_right_edge() - overlay._PANEL_X0
    right = f.shape[1] - overlay._PANEL_X0 - overlay.watch_panel_left_edge(f.shape[1])
    assert left == right


def test_watch_overlay_with_data_does_not_crash():
    f = _frame()
    draw_watch_overlay(f, _watch())
    draw_watch_overlay(f, None)
    assert bool(np.any(f))


def test_i_valori_piu_larghi_dell_orologio_restano_nel_pannello():
    f = _frame()
    draw_watch_overlay(f, _watch(bpm=220, spo2=100))
    assert not bool(np.any(f[:220, : overlay.watch_panel_left_edge(f.shape[1]), :]))


def test_il_pannello_dell_orologio_si_ridisegna_quando_cambiano_i_valori():
    overlay._WATCH_PANEL_CACHE.clear()
    draw_watch_overlay(_frame(), _watch(bpm=72))
    primo = overlay._WATCH_PANEL_CACHE[(True, 72, 98)]
    draw_watch_overlay(_frame(), _watch(bpm=72))
    assert overlay._WATCH_PANEL_CACHE[(True, 72, 98)] is primo
    draw_watch_overlay(_frame(), _watch(bpm=73))
    assert list(overlay._WATCH_PANEL_CACHE) == [(True, 73, 98)]
    overlay._WATCH_PANEL_CACHE.clear()


def _pixel_del_colore(frame, rgb):
    colore = np.array(rgb[::-1], dtype=int)
    return int((np.abs(frame.astype(int) - colore).sum(axis=2) <= 30).sum())


def test_l_icona_del_valore_e_rossa_solo_fuori_soglia():
    soglie = overlay.APP_CONFIG.smartwatch_thresholds
    normali, battito_alto, saturazione_bassa = _frame(), _frame(), _frame()
    draw_watch_overlay(normali, _watch(bpm=72, spo2=98))
    draw_watch_overlay(battito_alto, _watch(bpm=soglie.bpm_max_in + 1, spo2=98))
    draw_watch_overlay(saturazione_bassa, _watch(bpm=72, spo2=soglie.spo2_min_in - 1))
    assert _pixel_del_colore(normali, overlay._STATUS_OFF) == 0
    assert _pixel_del_colore(battito_alto, overlay._STATUS_OFF) > 20
    assert _pixel_del_colore(saturazione_bassa, overlay._STATUS_OFF) > 20


def test_senza_lettura_le_icone_dei_valori_non_sono_verdi_ne_rosse():
    normali, senza_lettura = _frame(), _frame()
    draw_watch_overlay(normali, _watch(bpm=72, spo2=98))
    draw_watch_overlay(senza_lettura, _watch(bpm=None, spo2=None))
    assert _pixel_del_colore(senza_lettura, overlay._STATUS_OFF) == 0
    # Resta verde solo l'icona della connessione.
    assert (_pixel_del_colore(senza_lettura, overlay._STATUS_ON)
            < _pixel_del_colore(normali, overlay._STATUS_ON))


def test_la_connessione_dell_orologio_cambia_colore():
    spento, acceso = _frame(), _frame()
    draw_watch_overlay(spento, _watch(connected=False))
    draw_watch_overlay(acceso, _watch(connected=True))
    assert not np.array_equal(spento, acceso)


def test_project_title_is_centered_at_the_top():
    f = _frame()
    draw_project_title(f, "WORK-GUARDIAN")
    colonne = np.where((f > 0).any(axis=2).any(axis=0))[0]
    centro = (int(colonne.min()) + int(colonne.max())) / 2.0
    assert abs(centro - f.shape[1] / 2.0) <= 4
    righe = np.where((f > 0).any(axis=2).any(axis=1))[0]
    assert int(righe.max()) < overlay.project_title_bottom_edge()


def test_project_title_is_cyan_like_the_panel_borders():
    assert overlay._TITLE_COLOR == overlay._PANEL_EDGE
    f = np.full((120, 960, 3), 255, dtype=np.uint8)
    draw_project_title(f, "WORK-GUARDIAN")
    azzurro = np.array(overlay._TITLE_COLOR[::-1], dtype=int)
    vicini = np.abs(f.astype(int) - azzurro).sum(axis=2) <= 12
    assert int(vicini.sum()) > 200


def test_the_watch_panel_says_connected_like_the_drone_panel():
    assert overlay._WATCH_LABELS == ("Connessione",)
    assert overlay._WATCH_LABELS[0] in overlay._PANEL_LABELS


def test_le_voci_dei_pannelli_vengono_da_una_lista_sola():
    righe = overlay._status_rows(True, False, True, False, True)
    assert [etichetta for etichetta, _stato, _icona in righe] == list(overlay._PANEL_LABELS)
    assert [stato for _etichetta, stato, _icona in righe] == [True, False, True, False, True]
    assert [etichetta for etichetta, _stato, _icona in overlay._watch_rows(False)] == list(
        overlay._WATCH_LABELS
    )


def test_la_voce_spenta_si_scrive_piu_spenta_di_quella_accesa():
    assert overlay._LABEL_OFF != overlay._LABEL
    assert sum(overlay._LABEL_OFF) < sum(overlay._LABEL)


def test_empty_project_title_is_noop():
    f = _frame()
    draw_project_title(f, "")
    assert not _changed(f)


def test_status_overlay_battery_none_does_not_crash():
    f = _frame()
    draw_status_overlay(f, _status(battery=None))
    assert bool(np.any(f))


def _drawn_right_edge(frame):
    painted = np.where((frame > 0).any(axis=2).any(axis=0))[0]
    return int(painted.max()) + 1


def test_status_panel_right_edge_matches_the_drawn_panel():
    frame = np.zeros((720, 960, 3), dtype=np.uint8)
    draw_status_overlay(frame, {"connected": True, "flying": True, "battery": 74})
    right_edge = _drawn_right_edge(frame)
    declared = overlay.status_panel_right_edge()
    assert right_edge <= declared, (
        f"il pannello arriva a x={right_edge} ma status_panel_right_edge dichiara "
        f"{declared}: il banner ci finirebbe sopra"
    )
    assert declared - right_edge <= 20, (
        f"il bordo dichiarato ({declared}) è molto più a destra del pannello reale "
        f"(x={right_edge}): il banner resterebbe staccato"
    )


def test_status_overlay_does_not_crash_on_small_frame():
    f = np.zeros((120, 130, 3), dtype=np.uint8)
    draw_status_overlay(f, _status())
    assert f.shape == (120, 130, 3)


def test_pannello_stato_disegnato_una_volta_sola_a_dati_invariati():
    with _CountingBuilder() as builder:
        for _ in range(20):
            draw_status_overlay(_frame(), _status(), True, True)
        assert builder.calls == 1, f"disegnato {builder.calls} volte invece di 1"


def test_pannello_stato_ridisegnato_quando_cambia_la_carica():
    with _CountingBuilder() as builder:
        draw_status_overlay(_frame(), _status(78), True, True)
        draw_status_overlay(_frame(), _status(77), True, True)
        assert builder.calls == 2


def test_pannello_stato_ridisegnato_quando_cambia_uno_stato():
    with _CountingBuilder() as builder:
        draw_status_overlay(_frame(), _status(), False, False)
        draw_status_overlay(_frame(), _status(), True, False)
        draw_status_overlay(_frame(), _status(), True, True)
        draw_status_overlay(_frame(), _status(connected=False), True, True)
        assert builder.calls == 4


def test_pannello_stato_cache_non_cresce():
    with _CountingBuilder():
        for battery in range(100, 60, -1):
            draw_status_overlay(_frame(), _status(battery), True, True)
        assert len(overlay._STATUS_PANEL_CACHE) == 1


def test_pannello_stato_dalla_cache_identico_al_disegno_diretto():
    overlay._STATUS_PANEL_CACHE.clear()
    primo = _frame()
    draw_status_overlay(primo, _status(), True, True)
    dalla_cache = _frame()
    draw_status_overlay(dalla_cache, _status(), True, True)

    overlay._STATUS_PANEL_CACHE.clear()
    senza_cache = _frame()
    draw_status_overlay(senza_cache, _status(), True, True)

    assert np.array_equal(primo, dalla_cache), "il frame dalla cache differisce dal primo"
    assert np.array_equal(primo, senza_cache), "il disegno da zero differisce dal primo"
    overlay._STATUS_PANEL_CACHE.clear()


def test_present_banner_is_drawn():
    f = _frame()
    draw_safety_net_verdict_banner(
        f, {"waypoint": 2, "outcome": "present", "tags": [4, 5, 13], "seen_tags": [13]}
    )
    assert _changed(f)
    assert f.shape == (720, 960, 3)


def _painted_box(frame):
    cols = np.where((frame > 0).any(axis=2).any(axis=0))[0]
    rows = np.where((frame > 0).any(axis=2).any(axis=1))[0]
    return int(cols.min()), int(cols.max()), int(rows.min()), int(rows.max())


_VERDICT = {"waypoint": 2, "outcome": "present", "tags": [4, 5, 13], "seen_tags": [13]}


def test_banner_stays_between_the_two_status_panels():
    f = _frame()
    draw_safety_net_verdict_banner(f, _VERDICT)
    left_edge, right_edge, _top, _bottom = _painted_box(f)
    assert left_edge >= overlay.status_panel_right_edge(), (
        f"il banner inizia a x={left_edge} ma il pannello del drone arriva a "
        f"{overlay.status_panel_right_edge()}: si sovrappongono"
    )
    assert right_edge <= overlay.watch_panel_left_edge(f.shape[1]), (
        f"il banner arriva a x={right_edge} ma il pannello dell'orologio inizia a "
        f"{overlay.watch_panel_left_edge(f.shape[1])}: si sovrappongono"
    )


def test_banner_sits_just_below_the_project_title():
    f = _frame()
    draw_safety_net_verdict_banner(f, _VERDICT)
    _left, _right, top, _bottom = _painted_box(f)
    titolo_sotto = overlay.project_title_bottom_edge()
    assert top >= titolo_sotto, (
        f"il banner inizia a y={top}, sopra la fascia del titolo ({titolo_sotto})"
    )
    assert top - titolo_sotto <= 24, (
        f"il banner ({top}) è staccato dal titolo ({titolo_sotto})"
    )


def test_missing_banner_is_drawn():
    f = _frame()
    draw_safety_net_verdict_banner(
        f, {"waypoint": 2, "outcome": "missing", "tags": [4, 5, 13], "seen_tags": [13]}
    )
    assert _changed(f)


def test_no_tags_banner_is_drawn():
    f = _frame()
    draw_safety_net_verdict_banner(
        f, {"waypoint": 6, "outcome": "no_tags", "tags": [2, 16], "seen_tags": []}
    )
    assert _changed(f)


def test_empty_or_none_verdict_is_noop():
    f = _frame()
    draw_safety_net_verdict_banner(f, None)
    draw_safety_net_verdict_banner(f, {})
    assert not _changed(f)


def test_unknown_outcome_is_noop():
    f = _frame()
    draw_safety_net_verdict_banner(f, {"waypoint": 1, "outcome": "boh", "tags": [3], "seen_tags": [3]})
    assert not _changed(f)


def test_missing_seen_tags_keys_do_not_crash():
    f = _frame()
    draw_safety_net_verdict_banner(f, {"outcome": "present"})
    assert _changed(f)


def test_banner_stays_within_frame_bounds():
    f = np.zeros((480, 640, 3), dtype=np.uint8)
    draw_safety_net_verdict_banner(
        f, {"waypoint": 7, "outcome": "missing", "tags": [8, 9, 11], "seen_tags": [9]}
    )
    assert bool(np.any(f))
    assert f.shape == (480, 640, 3)


def test_il_pannello_elenca_anche_il_controller():
    assert "Controller" in overlay._PANEL_LABELS


def test_il_pannello_si_ridisegna_quando_il_joystick_cambia():
    with _CountingBuilder() as builder:
        draw_status_overlay(_frame(), _status(joystick=True))
        draw_status_overlay(_frame(), _status(joystick=True))
        draw_status_overlay(_frame(), _status(joystick=False))
    assert builder.calls == 2


def test_il_suggerimento_dello_scenario_si_disegna_in_basso():
    frame = _frame()
    prima = frame.copy()
    overlay.draw_scenario_hint(frame, "L1")
    h = frame.shape[0]
    assert not np.array_equal(frame, prima)
    assert _row_has_content(frame, h - 40)
    assert not _row_has_content(frame, h // 2)


def test_il_suggerimento_non_esce_dal_fotogramma_piccolo():
    frame = np.zeros((90, 120, 3), dtype=np.uint8)
    overlay.draw_scenario_hint(frame, "L1")
    assert not np.any(frame)


def test_il_suggerimento_riusa_il_disegno():
    overlay._HINT_CACHE.clear()
    overlay.draw_scenario_hint(_frame(), "L1")
    primo = overlay._HINT_CACHE[("L1", overlay.SCENARIO_HINT_TEXT)]
    overlay.draw_scenario_hint(_frame(), "L1")
    assert overlay._HINT_CACHE[("L1", overlay.SCENARIO_HINT_TEXT)] is primo
    overlay.draw_scenario_hint(_frame(), "R1")
    assert ("L1", overlay.SCENARIO_HINT_TEXT) not in overlay._HINT_CACHE


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
