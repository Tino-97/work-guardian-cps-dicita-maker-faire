from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
from PIL import Image

from drone.config import APP_CONFIG
from drone.ui import fonts
from drone.ui.video import panels
from drone.ui.video.mission_map import LEGEND_SCENE, LEGEND_WAYPOINTS


def test_log_line_short_enough_is_left_alone():
    riga = "10:06:19  !  Una persona in un'area vietata"
    assert panels._layout_log_line(riga, 60) == [(riga, False)]


def test_log_line_too_long_wraps_instead_of_truncating():
    riga = "10:06:19  !  " + ("molto lunga " * 5).strip()
    righe = panels._layout_log_line(riga, 30)
    assert len(righe) > 1
    assert all(len(testo) <= 30 for testo, _ in righe)
    assert not any(testo.endswith("…") for testo, _ in righe)
    assert righe[0][1] is False
    assert all(rientrata for _, rientrata in righe[1:])


def test_wrapped_log_line_keeps_the_whole_message():
    riga = "10:06:42  ×  Rete di sicurezza mancante al waypoint 11"
    righe = panels._layout_log_line(riga, 25)
    assert righe[0][0].startswith("10:06:42  ×  Rete")
    ricomposta = " ".join(testo for testo, _ in righe)
    assert ricomposta == riga


def test_wrap_text_short_line_unchanged():
    assert panels._wrap_text("riga corta", 40) == ["riga corta"]


def test_wrap_text_wraps_on_spaces_within_limit():
    pieces = panels._wrap_text("alfa beta gamma delta", 10)
    assert all(len(p) <= 10 for p in pieces)
    assert " ".join(pieces) == "alfa beta gamma delta"


def test_wrap_text_hard_breaks_long_token():
    path = "C:/Users/faiel/Desktop/DJI_volo_2/drone/flight_sessions/log.txt"
    pieces = panels._wrap_text(path, 12)
    assert all(len(p) <= 12 for p in pieces)
    assert "".join(pieces) == path


def test_alert_line_color_by_glyph():
    def line(glyph):
        return f"12:00:00  {glyph}  messaggio"

    assert panels.alert_line_color(line("×")) == panels._NEG
    assert panels.alert_line_color(line("!")) == panels._WARN
    assert panels.alert_line_color(line("•")) == panels._ACCENT
    assert panels.alert_line_color(line("·")) == panels._TEXT


def test_terminal_line_color_ignores_the_alert_glyph():
    def line(glyph):
        return f"12:00:00  {glyph}  messaggio"

    assert panels.terminal_line_color(line("×")) == panels._NEG
    assert panels.terminal_line_color(line("!")) == panels._WARN
    assert panels.terminal_line_color(line("•")) == panels._TEXT


def test_truncate_to_width_adds_ellipsis_and_fits():
    font = fonts.mono(16)
    truncated = panels._truncate_to_width("x" * 500, font, 100)
    assert truncated.endswith("…")
    assert font.getlength(truncated) <= 100
    assert panels._truncate_to_width("breve", font, 1000) == "breve"
    assert panels._truncate_to_width("x", font, 0) == ""


def test_the_legend_fits_the_information_column():
    font = panels._legend_font()
    larghezza = (
        2 * panels._LEGEND_PAD_X + panels._LEGEND_COL_GAP
        + panels._legend_column_width(LEGEND_WAYPOINTS, font)
        + panels._legend_column_width(LEGEND_SCENE, font)
    )
    assert larghezza <= APP_CONFIG.dashboard.map_info_col_width


def test_the_two_logs_keep_six_visible_rows():
    config = APP_CONFIG.dashboard
    altezza = config.video_size[1] - config.gap_px - panels.map_panel_height(config)
    _bordo_sx, bordo_alto, _bordo_dx, bordo_basso = panels.LOG_INSET_LEFT
    utile = (altezza - bordo_basso - 6) - (bordo_alto + panels._LOG_TEXT_TOP)
    assert utile // panels._LOG_LINE_H >= 6, (
        f"il pannello del log mostra {utile // panels._LOG_LINE_H} righe: "
        "la mappa si è presa troppo spazio"
    )


def test_the_map_starts_with_the_timer_and_ends_with_the_legend():
    config = APP_CONFIG.dashboard
    layout = panels._map_layout(config, panels.map_panel_height(config))
    _x0, mappa_sopra, _x1, mappa_sotto = layout["map"]
    _rx0, colonna_sopra, _rx1, colonna_sotto = layout["rail"]
    assert mappa_sopra == colonna_sopra
    assert mappa_sotto == colonna_sotto
    assert colonna_sopra - layout["header"][3] == panels._RAIL_GAP


def test_the_disclaimer_stays_inside_the_map_panel():
    config = APP_CONFIG.dashboard
    altezza = panels.map_panel_height(config)
    layout = panels._map_layout(config, altezza)
    righe = len(panels._disclaimer_lines(config.panel_width))
    assert layout["disclaimer_y"] + righe * panels._DISCLAIMER_LINE_H <= altezza


def test_the_disclaimer_names_meters_and_degrees():
    assert "metri o gradi" in panels.MAP_DISCLAIMER
    assert "m/°" not in panels.MAP_DISCLAIMER


def test_the_legend_labels_are_centered_on_their_symbols():
    img = Image.new("RGB", (300, 60), (0, 0, 0))
    bianco = (255, 255, 255)
    panels._draw_legend_column(img, 0, 0, [("xxxx", "square", bianco, bianco)], panels._legend_font())
    arr = np.asarray(img)

    def centro(colonne):
        righe = np.where(arr[:, colonne].any(axis=2).any(axis=1))[0]
        return (int(righe.min()) + int(righe.max())) / 2

    simbolo = centro(slice(0, panels._LEGEND_SWATCH + 1))
    testo = centro(slice(panels._LEGEND_SWATCH + panels._LEGEND_SWATCH_GAP, 300))
    assert abs(simbolo - testo) <= 1, f"simbolo a y={simbolo}, scritta a y={testo}"


_RIGA_LUNGA = (
    "10:06:14  ×  Batteria bassa (29%): rientro alla home non disponibile, "
    "atterraggio di sicurezza sul posto"
)
_RIGA_CON_PERCORSO = (
    "10:06:19  ×  Si è verificato un errore imprevisto: "
    "C:/Users/faiel/Desktop/WORKGUARDIAN_CPS/drone/flight_sessions/flight_data.xlsx"
)


def _log_panel_geometry():
    config = APP_CONFIG.dashboard
    gap = config.gap_px
    altezza = config.video_size[1] - gap - panels.map_panel_height(config)
    sinistra = (config.panel_width - gap) // 2
    destra = config.panel_width - gap - sinistra
    return config, altezza, (
        (config.terminal_title, sinistra, panels.LOG_INSET_LEFT, panels.terminal_line_color),
        (config.alerts_title, destra, panels.LOG_INSET_RIGHT, panels.alert_line_color),
    )


def test_a_long_message_stays_inside_the_two_log_panels():
    config, altezza, pannelli = _log_panel_geometry()
    righe = [_RIGA_LUNGA, _RIGA_CON_PERCORSO]
    for titolo, larghezza, inset, colore in pannelli:
        vuoto = panels.text_panel(
            config, titolo, [], altezza, engaged=True,
            line_color=colore, width=larghezza, inset=inset,
        )
        pieno = panels.text_panel(
            config, titolo, righe, altezza, engaged=True,
            line_color=colore, width=larghezza, inset=inset,
        )
        ys, xs = np.nonzero(np.any(vuoto != pieno, axis=2))
        assert len(xs) > 0, f"{titolo}: nessun testo disegnato"

        bordo_sx, bordo_alto, bordo_dx, bordo_basso = inset
        x_min = bordo_sx + panels._LOG_TEXT_PAD
        x_max = (larghezza - bordo_dx) - panels._LOG_TEXT_PAD
        y_min = bordo_alto + panels._LOG_TEXT_TOP
        y_max = (altezza - bordo_basso) - 6
        assert x_min <= xs.min() and xs.max() <= x_max, (
            f"{titolo}: testo da x={xs.min()} a x={xs.max()}, area {x_min}..{x_max}"
        )
        assert y_min <= ys.min() and ys.max() <= y_max, (
            f"{titolo}: testo da y={ys.min()} a y={ys.max()}, area {y_min}..{y_max}"
        )


def test_the_oldest_message_leaves_the_panel_whole_and_not_headless():
    righe = [
        "10:06:10  !  Drone rilevato a terra mentre risultava in volo: "
        "autonomia disattivata e stato riallineato",
        _RIGA_LUNGA,
    ]
    pezzi = panels._visible_log_pieces(righe, 46, 6)
    assert pezzi[0][1] is False
    assert {riga for _testo, _rientrata, riga in pezzi} == {_RIGA_LUNGA}


def test_a_message_taller_than_the_panel_keeps_its_head():
    pezzi = panels._visible_log_pieces([_RIGA_LUNGA], 46, 3)
    assert [(testo, rientrata) for testo, rientrata, _riga in pezzi] == (
        panels._layout_log_line(_RIGA_LUNGA, 46)[:3]
    )
    assert pezzi[0][0].startswith("10:06:14")


def test_short_messages_still_fill_the_panel():
    righe = [f"10:06:{i:02d}  ·  Waypoint {i} raggiunto" for i in range(8)]
    pezzi = panels._visible_log_pieces(righe, 46, 6)
    assert [testo for testo, _rientrata, _riga in pezzi] == righe[2:]


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
