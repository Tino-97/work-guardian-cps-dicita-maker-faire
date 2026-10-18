from __future__ import annotations

import ast
import contextlib
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import drone.ui.console as co
from drone.config import APP_CONFIG, BASE_DIR
from drone.geometry.positions import normalize_position_world
from drone.ui import fonts
from drone.ui.video import panels
from drone.ui.console import (
    ConsoleLogFormatter,
    print_event,
    log_console_block,
    log_waypoint_reached,
    print_step,
    project_error_text,
    reset_mission_state,
    set_alert_sink,
)


def _capture(fn) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def _record(level: int, msg: str, *, phase: bool = False, title: bool = False) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="drone.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    if phase:
        rec.phase = True
    if title:
        rec.title = True
    return rec


def test_format_info_line_has_glyph_and_no_module_path():
    out = ConsoleLogFormatter().format(_record(logging.INFO, "Connesso al Tello."))
    assert "Connesso al Tello." in out
    assert "drone.test" not in out
    assert out[2] == ":" and out[5] == ":"


def test_format_level_glyphs():
    fmt = ConsoleLogFormatter()
    assert "!" in fmt.format(_record(logging.WARNING, "x"))
    assert "×" in fmt.format(_record(logging.ERROR, "x"))
    assert "·" in fmt.format(_record(logging.DEBUG, "x"))


def test_phase_record_rendered_as_titled_rule():
    out = ConsoleLogFormatter().format(_record(logging.INFO, "Fase 4 · Connessione al drone", phase=True))
    assert "FASE 4 · CONNESSIONE AL DRONE" in out
    assert "----" in out
    assert "──" not in out
    assert out.count("\n") == 1


def test_title_record_is_centered_above_a_rule():
    out = ConsoleLogFormatter().format(_record(logging.INFO, "WORK-GUARDIAN", title=True))
    riga_titolo, riga_linea = out.split("\n")
    assert riga_titolo.strip() == "WORK-GUARDIAN"
    assert riga_titolo.startswith(" ")
    assert set(riga_linea) == {"-"}


def test_log_title_goes_to_the_phase_logger():
    recs = _collect_logs("drone.phase", lambda: co.log_title("WORK-GUARDIAN"))
    assert len(recs) == 1
    assert recs[0].title is True
    assert recs[0].getMessage() == "WORK-GUARDIAN"


def test_print_step_goes_to_shell_not_stdout():
    assert _capture(lambda: print_step("OK", "Joystick inizializzato.")) == ""


def test_print_step_format():
    recs = _collect_logs("drone.setup", lambda: print_step("OK", "Joystick inizializzato."))
    assert len(recs) == 1
    out = ConsoleLogFormatter().format(recs[0]).rstrip("\n")
    assert out.endswith("✓  Joystick inizializzato.")
    assert out[2] == ":" and out[5] == ":"
    assert "OK" not in out


def test_log_console_block_is_raw():
    recs = _collect_logs("drone.setup", lambda: log_console_block("Sistema pronto.\nriga 2"))
    assert len(recs) == 1
    out = ConsoleLogFormatter().format(recs[0])
    assert out == "Sistema pronto.\nriga 2"


def test_ready_banner_is_only_the_titled_rule_and_the_message():
    recs = _collect_logs("drone.setup", lambda: co.log_ready_banner(
        "Tutto pronto", "Il drone è a terra.",
    ))
    assert len(recs) == 1
    righe = ConsoleLogFormatter().format(recs[0]).split("\n")
    assert righe[0] == ""
    assert righe[1].startswith("-") and "TUTTO PRONTO" in righe[1]
    assert righe[2:] == ["Il drone è a terra."]


def test_print_event_setup_goes_to_shell_not_panel():
    assert _capture(lambda: print_event("x")) == ""
    assert _capture(lambda: print_event("x", prefix="AVVISO")) == ""
    assert _capture(lambda: print_event("x", prefix="ERRORE")) == ""
    assert _capture(lambda: print_event("x", prefix="RETE")) == ""


def test_print_event_runtime_goes_to_panel_not_shell():
    prev = co._runtime_started
    co._runtime_started = True
    try:
        assert "·" in _capture(lambda: print_event("x"))
        assert "!" in _capture(lambda: print_event("x", prefix="AVVISO"))
        assert "×" in _capture(lambda: print_event("x", prefix="ERRORE"))
        assert "•" in _capture(lambda: print_event("x", prefix="RETE"))
        recs = _collect_logs("drone.event", lambda: print_event("x", prefix="AVVISO"))
        assert recs == []
    finally:
        co._runtime_started = prev


def test_print_event_alert_channel_goes_to_sink_in_flight():
    prev = co._runtime_started
    co._runtime_started = True
    sink_lines = []
    set_alert_sink(sink_lines.append)
    try:
        out = _capture(lambda: print_event("Rilevata la caduta di una persona.",
                                            prefix="AVVISO", channel="alert"))
        assert out == ""
        assert len(sink_lines) == 1
        assert "!" in sink_lines[0]
        assert "caduta di una persona" in sink_lines[0]
    finally:
        set_alert_sink(None)
        co._runtime_started = prev


def test_print_event_safety_net_missing_uses_critical_glyph():
    prev = co._runtime_started
    co._runtime_started = True
    sink_lines = []
    set_alert_sink(sink_lines.append)
    try:
        _capture(lambda: print_event("Rete di sicurezza mancante al waypoint 2",
                                      prefix="ALLERTA", channel="alert"))
        assert len(sink_lines) == 1
        assert "×" in sink_lines[0]
        assert "!" not in sink_lines[0]
    finally:
        set_alert_sink(None)
        co._runtime_started = prev


def test_print_event_alert_channel_falls_back_to_print_without_sink():
    prev = co._runtime_started
    co._runtime_started = True
    try:
        out = _capture(lambda: print_event("Rete di sicurezza presente.",
                                            prefix="RETE", channel="alert"))
        assert "•" in out and "Rete di sicurezza presente." in out
    finally:
        co._runtime_started = prev


def test_print_event_alert_channel_falls_back_when_sink_raises():
    prev = co._runtime_started
    co._runtime_started = True

    def bad_sink(_line):
        raise RuntimeError("pannello rotto")

    set_alert_sink(bad_sink)
    try:
        out = _capture(lambda: print_event("Allerta.", prefix="AVVISO", channel="alert"))
        assert "Allerta." in out
    finally:
        set_alert_sink(None)
        co._runtime_started = prev


def test_print_event_drone_channel_ignores_alert_sink():
    prev = co._runtime_started
    co._runtime_started = True
    sink_lines = []
    set_alert_sink(sink_lines.append)
    try:
        out = _capture(lambda: print_event("Decollo eseguito."))
        assert "Decollo eseguito." in out
        assert sink_lines == []
    finally:
        set_alert_sink(None)
        co._runtime_started = prev


def _collect_logs(logger_name: str, fn):
    logger = logging.getLogger(logger_name)
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        _capture(fn)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
    return records


def test_print_event_avviso_logs_warning():
    recs = _collect_logs("drone.event", lambda: print_event("Batteria bassa", prefix="AVVISO"))
    assert len(recs) == 1 and recs[0].levelno == logging.WARNING
    assert "Batteria bassa" in recs[0].getMessage()


def test_print_event_errore_logs_error():
    recs = _collect_logs("drone.event", lambda: print_event("Timeout video", prefix="ERRORE"))
    assert len(recs) == 1 and recs[0].levelno == logging.ERROR


def test_print_event_evento_logs_info_to_shell_during_setup():
    recs = _collect_logs("drone.event", lambda: print_event("Waypoint 2 raggiunto."))
    assert len(recs) == 1 and recs[0].levelno == logging.INFO


def test_print_event_rete_logs_info_to_shell_during_setup():
    recs = _collect_logs("drone.event", lambda: print_event("Rete rilevata", prefix="RETE"))
    assert len(recs) == 1 and recs[0].levelno == logging.INFO


def _in_volo(fn) -> str:
    prev = co._runtime_started
    co._runtime_started = True
    try:
        return _capture(fn)
    finally:
        co._runtime_started = prev


def test_waypoint_reached_logged_once_per_waypoint():
    reset_mission_state()
    out = _in_volo(lambda: (
        log_waypoint_reached({"reached": True}, "7 di 11"),
        log_waypoint_reached({"reached": True}, "7 di 11"),
        log_waypoint_reached({"reached": True}, "8 di 11"),
    ))
    righe = [r for r in out.splitlines() if r.strip()]
    assert len(righe) == 2
    assert "Waypoint 7 di 11 raggiunto" in righe[0]
    assert "Waypoint 8 di 11 raggiunto" in righe[1]
    reset_mission_state()


def test_intermediate_states_are_not_logged():
    reset_mission_state()
    out = _in_volo(lambda: (
        log_waypoint_reached({"reached": False, "reason": "tracking"}, "7 di 11"),
        log_waypoint_reached({}, "7 di 11"),
        log_waypoint_reached(None, "7 di 11"),
    ))
    assert out.strip() == ""
    reset_mission_state()


def test_reset_mission_state_allows_relogging_same_waypoint():
    reset_mission_state()
    out = _in_volo(lambda: (
        log_waypoint_reached({"reached": True}, "7 di 11"),
        reset_mission_state(),
        log_waypoint_reached({"reached": True}, "7 di 11"),
    ))
    assert len([r for r in out.splitlines() if r.strip()]) == 2
    reset_mission_state()


def test_print_step_failure_logs_warning():
    logger = logging.getLogger("drone.setup")
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        _capture(lambda: print_step("!!", "Filtro Kalman non disponibile"))
        _capture(lambda: print_step("OK", "Tutto a posto"))
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "Filtro Kalman non disponibile" in records[0].getMessage()


def _record_livello(level, message="qualcosa"):
    return logging.LogRecord(
        "drone.test", level, __file__, 1, message, None, None,
    )


def _record_da(name, level=logging.WARNING, message="guaio", **attributi):
    record = logging.LogRecord(name, level, __file__, 1, message, None, None)
    for chiave, valore in attributi.items():
        setattr(record, chiave, valore)
    return record


def _formatta(record, *, in_volo=True) -> str:
    prev = co._runtime_started
    co._runtime_started = in_volo
    try:
        return ConsoleLogFormatter().format(record)
    finally:
        co._runtime_started = prev


def test_errors_in_flight_show_only_the_time_and_the_message():
    testo = _formatta(_record_livello(logging.ERROR, "batteria bassa"))
    orario, messaggio = testo.split("  ", 1)
    assert len(orario) == 8 and orario[2] == ":" and orario[5] == ":"
    assert messaggio == "batteria bassa"


def test_setup_problems_keep_their_glyph():
    testo = _formatta(_record_livello(logging.ERROR, "guaio"), in_volo=False)
    assert "×  guaio" in testo


def test_setup_steps_keep_their_glyph_in_flight():
    testo = _formatta(_record_da("drone.setup", setup_label="!!"))
    assert "!  guaio" in testo


def _gestore_su_buffer():
    flusso = io.StringIO()
    gestore = co.ConsoleHandler(flusso)
    gestore.setFormatter(logging.Formatter("%(message)s"))
    return gestore, flusso


def test_the_handler_keeps_the_startup_lines_until_told_to_stop():
    gestore, flusso = _gestore_su_buffer()
    gestore.handle(_record_livello(logging.INFO, "fase 1"))
    gestore.end_startup()
    gestore.handle(_record_livello(logging.INFO, "fase 4"))
    assert flusso.getvalue() == "fase 1\nfase 4\n"
    assert gestore._startup_lines == ["fase 1"]


def test_the_redraw_clears_the_screen_and_rewrites_only_the_startup():
    gestore, flusso = _gestore_su_buffer()
    gestore.handle(_record_livello(logging.INFO, "fase 1"))
    gestore.end_startup()
    gestore.handle(_record_livello(logging.WARNING, "errore del volo precedente"))
    co.set_color_enabled(True)
    try:
        gestore.redraw_startup()
    finally:
        co.set_color_enabled(False)
    assert flusso.getvalue().split(co._CLEAR_SCREEN)[-1] == "fase 1\n"


def test_without_a_real_terminal_the_redraw_clears_nothing():
    gestore, flusso = _gestore_su_buffer()
    gestore.handle(_record_livello(logging.INFO, "fase 1"))
    gestore.end_startup()
    gestore.redraw_startup()
    assert flusso.getvalue() == "fase 1\n"


def test_the_console_leaves_flight_mode_when_asked():
    precedente = co._runtime_started
    try:
        co.mark_runtime_started()
        co.mark_runtime_stopped()
        assert co._runtime_started is False
    finally:
        co._runtime_started = precedente


def _filtro():
    orologio = [0.0]
    return co.RepeatedErrorFilter(5.0, time_source=lambda: orologio[0]), orologio


def test_the_same_error_is_not_repeated_within_the_interval():
    filtro, orologio = _filtro()
    assert filtro.filter(_record_da("drone.perception.pose_filter")) is True
    orologio[0] = 4.9
    assert filtro.filter(_record_da("drone.perception.pose_filter")) is False
    orologio[0] = 5.0
    assert filtro.filter(_record_da("drone.perception.pose_filter")) is True


def test_different_errors_are_limited_independently():
    filtro, _orologio = _filtro()
    assert filtro.filter(_record_da("drone.perception.pose_filter", message="primo")) is True
    assert filtro.filter(_record_da("drone.perception.pose_filter", message="secondo")) is True
    assert filtro.filter(_record_da("drone.perception.pose_estimator", message="primo")) is True


def test_the_limit_ignores_the_values_inside_the_message():
    filtro, _orologio = _filtro()
    modello = "Orientamento non ricavabile da un marker AprilTag (asse %s quasi verticale)"
    primo = logging.LogRecord("drone.perception.pose_estimator", logging.WARNING, __file__, 1, modello, ("x",), None)
    secondo = logging.LogRecord("drone.perception.pose_estimator", logging.WARNING, __file__, 1, modello, ("z",), None)
    assert filtro.filter(primo) is True
    assert filtro.filter(secondo) is False


def test_the_limit_leaves_setup_steps_and_informative_lines_alone():
    filtro, _orologio = _filtro()
    for _ in range(3):
        assert filtro.filter(_record_da("drone.setup", setup_label="!!")) is True
        assert filtro.filter(_record_da("drone.phase", level=logging.INFO)) is True


def test_flight_events_stay_out_of_the_error_log():
    prev = co._runtime_started
    co._runtime_started = True
    try:
        errori = _collect_logs("drone.event", lambda: print_event(
            "Batteria al 18%: atterro per sicurezza", prefix="ERRORE",
        ))
    finally:
        co._runtime_started = prev
    assert errori == []


_MESSAGGIO_LUNGO = (
    "Non è stato possibile creare l'autopilota perché la configurazione del "
    "percorso non è valida: il volo autonomo non è disponibile e si prosegue in manuale"
)
_PERCORSO_LUNGO = (
    "C:/Users/faiel/Desktop/WORKGUARDIAN_CPS/drone/waypoint_paths/sicurezza_operatore.json"
)


def test_the_shell_never_cuts_a_long_message():
    testo_atteso = f"{_MESSAGGIO_LUNGO} {_PERCORSO_LUNGO}"
    for in_volo in (False, True):
        for livello in (logging.INFO, logging.WARNING, logging.ERROR):
            testo = _formatta(_record_livello(livello, testo_atteso), in_volo=in_volo)
            assert testo_atteso in testo, f"livello {livello}, in volo {in_volo}"
            assert "…" not in testo


def test_the_shell_never_cuts_a_long_setup_step():
    recs = _collect_logs("drone.setup", lambda: print_step("!!", _MESSAGGIO_LUNGO))
    testo = ConsoleLogFormatter().format(recs[0])
    assert testo.endswith(_MESSAGGIO_LUNGO)


def test_a_phase_wider_than_the_terminal_keeps_its_whole_title():
    titolo = "Fase 7 · " + "Registrazione dei dati del volo " * 4
    testo = ConsoleLogFormatter().format(
        _record_da("drone.phase", logging.INFO, titolo, phase=True)
    )
    assert titolo.upper() in testo
    assert testo.endswith("----")


def test_a_ready_banner_wider_than_the_terminal_keeps_its_whole_title():
    titolo = "Pronto al decollo " * 6
    recs = _collect_logs("drone.setup", lambda: co.log_ready_banner(titolo, _MESSAGGIO_LUNGO))
    righe = ConsoleLogFormatter().format(recs[0]).split("\n")
    assert titolo.strip().upper() in righe[1]
    assert righe[2] == _MESSAGGIO_LUNGO


_MODULI_DI_PREPARAZIONE = (
    "drone/flight/preflight.py",
    "drone/flight/subsystem_builders.py",
)

_CAMPI_PIU_LUNGHI = {
    "battery": "100",
    "stato": "disattivato",
    "secondi": "99,9",
    "waypoint": "22",
    "label": "10 di 10",
    "detail or 'guasto'": "dati non validi",
    "float(remaining):.0f": "120",
    "_waypoint_label(autopilot_command, apriltag_autopilot)": "10 di 10",
    "testo_bpm": "250",
    "testo_spo2": "100%",
}


def _caratteri_per_riga(canale: str) -> int:
    config = APP_CONFIG.dashboard
    sinistra = (config.panel_width - config.gap_px) // 2
    if canale == "alert":
        larghezza = config.panel_width - config.gap_px - sinistra
        inset = panels.LOG_INSET_RIGHT
    else:
        larghezza = sinistra
        inset = panels.LOG_INSET_LEFT
    bordo_sx, _alto, bordo_dx, _basso = inset
    disponibili = (
        (larghezza - bordo_dx - panels._LOG_TEXT_PAD)
        - (bordo_sx + panels._LOG_TEXT_PAD)
    )
    return int(disponibili / panels._mono_char_width(fonts.mono(panels._LOG_FONT_SIZE)))


def _testi_del_messaggio(nodo, campi_usati: set) -> list[str]:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return [nodo.value]
    if isinstance(nodo, ast.IfExp):
        return (
            _testi_del_messaggio(nodo.body, campi_usati)
            + _testi_del_messaggio(nodo.orelse, campi_usati)
        )
    if isinstance(nodo, ast.JoinedStr):
        testo = ""
        for parte in nodo.values:
            if isinstance(parte, ast.Constant):
                testo += parte.value
                continue
            campo = ast.unparse(parte)[1:-1]
            campi_usati.add(campo)
            assert campo in _CAMPI_PIU_LUNGHI, (
                f"il campo {{{campo}}} non ha una larghezza dichiarata in _CAMPI_PIU_LUNGHI"
            )
            testo += _CAMPI_PIU_LUNGHI[campo]
        return [testo]
    return []


def _moduli_del_package():
    for percorso in sorted((BASE_DIR / "drone").rglob("*.py")):
        if "tests" in percorso.parts:
            continue
        if percorso.relative_to(BASE_DIR).as_posix() in _MODULI_DI_PREPARAZIONE:
            continue
        yield percorso


def _messaggi_degli_eventi(campi_usati: set) -> list[tuple[str, str, str]]:
    trovati = []
    for percorso in _moduli_del_package():
        relativo = percorso.relative_to(BASE_DIR).as_posix()
        albero = ast.parse(percorso.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if not isinstance(nodo, ast.Call) or not nodo.args:
                continue
            if getattr(nodo.func, "id", "") != "print_event":
                continue
            canale = next(
                (
                    k.value.value
                    for k in nodo.keywords
                    if k.arg == "channel" and isinstance(k.value, ast.Constant)
                ),
                "drone",
            )
            for testo in _testi_del_messaggio(nodo.args[0], campi_usati):
                trovati.append((relativo, canale, testo))
    return trovati


def test_every_event_message_fits_one_line_of_the_panel():
    campi_usati: set = set()
    messaggi = _messaggi_degli_eventi(campi_usati)
    assert len(messaggi) > 30, f"trovati solo {len(messaggi)} messaggi: l'estrazione non funziona"

    for modulo, canale, testo in messaggi:
        massimo = _caratteri_per_riga(canale)
        riga = co._console_line("INFO", testo)
        righe = panels._layout_log_line(riga, massimo)
        assert len(righe) == 1, (
            f"{modulo}: «{testo}» occupa {len(righe)} righe "
            f"({len(riga)} caratteri, il pannello ne tiene {massimo})"
        )


def test_no_unused_field_width_is_declared():
    campi_usati: set = set()
    _messaggi_degli_eventi(campi_usati)
    assert set(_CAMPI_PIU_LUNGHI) == campi_usati


def _raised(fn) -> Exception:
    try:
        fn()
    except Exception as exc:
        return exc
    raise AssertionError("doveva sollevare un errore")


def test_a_project_error_keeps_its_italian_text():
    exc = _raised(lambda: normalize_position_world([1.0, 2.0]))
    assert project_error_text(exc).startswith(
        "position_world deve contenere esattamente 3 coordinate"
    )


def test_an_error_raised_by_a_library_is_hidden():
    exc = _raised(lambda: json.loads("{"))
    assert project_error_text(exc) is None


def test_a_python_error_inside_project_code_is_hidden():
    exc = _raised(lambda: normalize_position_world(["a", 1.0, 2.0]))
    assert isinstance(exc, ValueError)
    assert project_error_text(exc) is None


def test_an_error_never_raised_has_no_text():
    assert project_error_text(RuntimeError("mai sollevato")) is None


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
