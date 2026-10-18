from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from biometric_log_samples import logger_con_sessione, telemetria
from drone.data.biometric_data_logger import BiometricDataLogger


def _matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def test_la_telemetria_tiene_grezzo_e_filtrato():
    log = BiometricDataLogger()
    assert log.log_message(telemetria(80.2, 75.4, 96.0, 97.1), timestamp=1.0)

    campione = log.samples()[0]
    assert campione["hr_grezzo"] == 80.2
    assert campione["hr_filtrato"] == 75.4
    assert campione["spo2_grezzo"] == 96.0
    assert campione["spo2_filtrato"] == 97.1
    assert campione["stato"] == "NORMALE"
    assert campione["lettura_valida"] is True


def test_la_telemetria_tiene_anche_i_valori_interi_confrontati_dal_firmware():
    log = BiometricDataLogger()
    log.log_message(telemetria(80.2, 75.4, 96.0, 97.1), timestamp=1.0)

    campione = log.samples()[0]
    assert campione["bpm"] == 75.0
    assert campione["spo2"] == 97.0


def test_un_valore_null_resta_un_buco_e_non_diventa_zero():
    log = BiometricDataLogger()
    log.log_message(telemetria(None, None, None, None, "RICERCA_SEGNALE"), timestamp=1.0)

    campione = log.samples()[0]
    for chiave in ("hr_grezzo", "hr_filtrato", "spo2_grezzo", "spo2_filtrato", "bpm", "spo2"):
        assert campione[chiave] is None
    assert campione["lettura_valida"] is False


def test_il_firmware_precedente_senza_grezzi_da_comunque_il_filtrato():
    log = BiometricDataLogger()
    log.log_message(
        {"bpm": 76, "spo2": 98, "stato": "NORMALE", "lettura_valida": True},
        timestamp=1.0,
    )

    campione = log.samples()[0]
    assert campione["hr_filtrato"] == 76.0
    assert campione["spo2_filtrato"] == 98.0
    assert campione["hr_grezzo"] is None


def test_l_allarme_biometrico_e_un_evento_e_non_un_campione():
    log = BiometricDataLogger()
    log.log_message({"bpm": 131, "spo2": 95, "evento": "BIOMETRIA_ANOMALA"}, timestamp=1.0)

    assert log.samples() == []
    eventi = log.events()
    assert len(eventi) == 1
    assert eventi[0]["bpm"] == 131.0


def test_un_messaggio_senza_stato_ne_evento_viene_ignorato():
    log = BiometricDataLogger()
    assert log.log_message({"ciao": 1}) is False
    assert log.samples() == []
    assert log.events() == []


def test_senza_almeno_due_campioni_non_ci_sono_dati_da_salvare():
    log = BiometricDataLogger()
    assert not log.has_data()
    log.log_message(telemetria(80.0, 75.0, 96.0, 97.0), timestamp=1.0)
    assert not log.has_data()
    log.log_message(telemetria(81.0, 75.5, 96.0, 97.0), timestamp=1.5)
    assert log.has_data()


def test_senza_dati_non_si_crea_la_cartella():
    with tempfile.TemporaryDirectory() as d:
        assert BiometricDataLogger().export_session(d) is None
        assert list(Path(d).iterdir()) == []


def test_la_sessione_finisce_in_una_cartella_con_data_e_ora():
    if not _matplotlib_available():
        return
    log = logger_con_sessione()
    with tempfile.TemporaryDirectory() as d:
        cartella = log.export_session(d)
        assert cartella is not None
        assert cartella.parent == Path(d)
        assert cartella.name.startswith("biometric_session_")
        nomi = {p.name for p in cartella.glob("*.png")}
    assert nomi == {
        "biometric_raw_and_filtered_heart_rate.png",
        "biometric_raw_and_filtered_spo2.png",
        "biometric_heart_rate_trend.png",
        "biometric_spo2_trend.png",
    }


def test_due_sessioni_nello_stesso_secondo_non_si_sovrascrivono():
    if not _matplotlib_available():
        return
    log = logger_con_sessione()
    with tempfile.TemporaryDirectory() as d:
        prima = log.export_session(d)
        seconda = log.export_session(d)
        assert prima is not None and seconda is not None
        assert prima != seconda


def test_il_riassunto_conta_campioni_e_allarmi():
    riassunto = logger_con_sessione().get_summary()
    assert riassunto == "Orologio: 40 campioni, 1 allarme biometrico"


def test_senza_messaggi_il_riassunto_lo_dice():
    assert BiometricDataLogger().get_summary() == "Nessun dato ricevuto dall'orologio"
