from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Optional

LOGGER = logging.getLogger(__name__)

BIOMETRIC_ALARM_EVENT = "BIOMETRIA_ANOMALA"


def _optional_float(value: Any) -> Optional[float]:
    # Il firmware manda null quando la lettura non c'è; un booleano o un testo
    # non sono una misura e diventano anch'essi un buco nei dati.
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BiometricDataLogger:
    # Raccoglie la telemetria che l'orologio pubblica via MQTT (un campione
    # ogni 0,5 s) e gli eventi di allarme biometrico. I messaggi arrivano dal
    # thread di paho, l'esportazione dal thread principale: per questo ogni
    # accesso passa dal lock.

    SESSION_PREFIX = "biometric_session"

    def __init__(self, max_samples: Optional[int] = None):
        self._max_samples = None if max_samples is None else max(1, int(max_samples))
        self._lock = threading.Lock()
        self._samples: deque[dict[str, Any]] = deque(maxlen=self._max_samples)
        self._events: list[dict[str, Any]] = []

    def log_message(
        self,
        message: Mapping[str, Any],
        timestamp: Optional[float] = None,
    ) -> bool:
        monotonic = None
        if timestamp is None:
            timestamp = time.time()
            monotonic = time.monotonic()
        tempo = {
            "timestamp": float(timestamp),
            **({} if monotonic is None else {"monotonic": float(monotonic)}),
        }

        if message.get("evento") == BIOMETRIC_ALARM_EVENT:
            event = {
                **tempo,
                "bpm": _optional_float(message.get("bpm")),
                "spo2": _optional_float(message.get("spo2")),
            }
            with self._lock:
                self._events.append(event)
            return True

        if "stato" not in message:
            return False

        valid = bool(message.get("lettura_valida", False))
        # Un firmware precedente manda solo bpm e spo2 arrotondati: diventano il
        # valore filtrato, e i grafici restano senza la serie grezza.
        hr_filtered = _optional_float(message.get("hr_filtrato"))
        spo2_filtered = _optional_float(message.get("spo2_filtrato"))
        if hr_filtered is None and "hr_filtrato" not in message and valid:
            hr_filtered = _optional_float(message.get("bpm"))
        if spo2_filtered is None and "spo2_filtrato" not in message and valid:
            spo2_filtered = _optional_float(message.get("spo2"))

        sample = {
            **tempo,
            "stato": str(message.get("stato")),
            "lettura_valida": valid,
            "hr_grezzo": _optional_float(message.get("hr_grezzo")),
            "hr_filtrato": hr_filtered,
            "spo2_grezzo": _optional_float(message.get("spo2_grezzo")),
            "spo2_filtrato": spo2_filtered,
            # Valori filtrati arrotondati all'intero, gli stessi che il firmware
            # confronta con le soglie dell'allarme.
            "bpm": _optional_float(message.get("bpm")),
            "spo2": _optional_float(message.get("spo2")),
        }
        with self._lock:
            self._samples.append(sample)
        return True

    def samples(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._samples)

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def has_data(self) -> bool:
        with self._lock:
            # Con meno di due campioni non c'è una linea da disegnare: la
            # cartella resterebbe vuota.
            return len(self._samples) >= 2

    def save_plots(self, output_dir: str | Path) -> list[Path]:
        from drone.ui.plots import flight_plots
        return flight_plots.save_biometric_plots(self, output_dir)

    EXCEL_FILENAME = "biometric_data.xlsx"

    TEXT_FILENAME = "biometric_watch_log.txt"

    @staticmethod
    def _app_config(app_config: Any):
        if app_config is not None:
            return app_config
        from drone.config import APP_CONFIG
        return APP_CONFIG

    def save_text_file(
        self, output_dir: str | Path, *, app_config: Any = None
    ) -> Optional[Path]:
        if not self.has_data():
            LOGGER.info("Nessun dato dall'orologio da esportare nel file di testo.")
            return None

        from drone.data import biometric_text_report

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        thresholds = self._app_config(app_config).smartwatch_thresholds

        try:
            return biometric_text_report.save_data_to_file(
                self, output_dir / self.TEXT_FILENAME, thresholds
            )
        except Exception:
            LOGGER.exception("Non è stato possibile salvare il file di testo dell'orologio")
            return None

    def _save_excel(
        self, session_dir: Path, *, app_config: Any = None
    ) -> Optional[Path]:
        try:
            from drone.data import biometric_excel_report
        except ImportError:
            LOGGER.info(
                "openpyxl non disponibile: i dati dell'orologio vengono salvati nel "
                "file di testo. Per avere il file Excel: pip install openpyxl."
            )
            return None

        try:
            app_config = self._app_config(app_config)
            parameters = biometric_excel_report.collect_session_parameters(
                self, app_config
            )
            return biometric_excel_report.save_session_workbook(
                self,
                session_dir / self.EXCEL_FILENAME,
                app_config.smartwatch_thresholds,
                parameters,
            )
        except Exception:
            LOGGER.exception(
                "Non è stato possibile scrivere il file Excel della sessione "
                "biometrica: viene salvato il file di testo"
            )
            return None

    def export_session(
        self, output_root: str | Path, *, app_config: Any = None
    ) -> Optional[Path]:
        if not self.has_data():
            LOGGER.info(
                "Nessun dato dall'orologio: la cartella della sessione biometrica "
                "non viene creata."
            )
            return None

        try:
            output_root = Path(output_root)
            session_name = time.strftime(f"{self.SESSION_PREFIX}_%d_%m_%Y_%H_%M_%S")
            session_dir = output_root / session_name
            if session_dir.exists():
                suffix = 2
                while (output_root / f"{session_name}_{suffix}").exists():
                    suffix += 1
                session_dir = output_root / f"{session_name}_{suffix}"
            session_dir.mkdir(parents=True, exist_ok=True)

            excel_path = self._save_excel(session_dir, app_config=app_config)
            if excel_path is None:
                self.save_text_file(session_dir, app_config=app_config)

            try:
                self.save_plots(session_dir)
            except Exception:
                LOGGER.exception("Non è stato possibile generare i grafici biometrici")

            return session_dir

        except Exception:
            LOGGER.exception("Non è stato possibile esportare la sessione biometrica")
            return None

    def get_summary(self) -> str:
        campioni = len(self.samples())
        allarmi = len(self.events())
        if campioni == 0 and allarmi == 0:
            return "Nessun dato ricevuto dall'orologio"
        testo_allarmi = (
            "nessun allarme biometrico" if allarmi == 0
            else "1 allarme biometrico" if allarmi == 1
            else f"{allarmi} allarmi biometrici"
        )
        return f"Orologio: {campioni} campioni, {testo_allarmi}"
