from __future__ import annotations

from collections import deque

import logging
import math

import time

from pathlib import Path

from typing import Any, Mapping, Optional

import numpy as np

from drone.geometry import (
    wrap_angle_deg as _wrap_angle_deg,
    normalize_position_world as _normalize_position_world,
)

from drone.data import flight_text_report

LOGGER = logging.getLogger(__name__)


class FlightDataLogger:
    TAG_IDS_SEPARATOR = "|"

    def __init__(
        self,
        output_dir: str | Path = ".",
        max_samples: Optional[int] = None,
    ):
        self.output_dir = Path(output_dir)

        self._max_samples: Optional[int] = (
            None if max_samples is None else max(1, int(max_samples))
        )

        self.comparison_entries: deque[dict[str, Any]] = deque(maxlen=self._max_samples)

        self.autopilot_entries: deque[dict[str, Any]] = deque(maxlen=self._max_samples)

        self._comparison_cap_warned = False
        self._autopilot_cap_warned = False

        self.autopilot_xy_tolerance_m: Optional[float] = None
        self.autopilot_z_tolerance_m: Optional[float] = None
        self.autopilot_yaw_tolerance_deg: Optional[float] = None

        self._error_log_throttle_sec = 5.0
        self._error_log_last_at: dict[str, float] = {}

        LOGGER.info("La registrazione dei dati di volo è stata avviata nella cartella %s", self.output_dir)

    @staticmethod
    def _get_mapping_value(mapping: Optional[Mapping[str, Any]], key: str, default: Any = None) -> Any:
        if mapping is None:
            return default
        try:
            return mapping.get(key, default)
        except AttributeError:
            return default

    @staticmethod
    def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default

        if isinstance(obj, Mapping):
            return obj.get(key, default)

        return getattr(obj, key, default)

    def has_data(self) -> bool:
        return len(self.comparison_entries) > 0 or len(self.autopilot_entries) > 0

    def _note_eviction_if_full(self, buffer: deque, buffer_label: str) -> None:
        if self._max_samples is None or len(buffer) < self._max_samples:
            return
        if buffer_label == "comparison":
            already_warned = self._comparison_cap_warned
            self._comparison_cap_warned = True
        else:
            already_warned = self._autopilot_cap_warned
            self._autopilot_cap_warned = True
        if not already_warned:
            serie = "di posizione" if buffer_label == "comparison" else "dell'autopilota"
            LOGGER.warning(
                "La memoria dei dati di volo è piena (%d campioni %s): da ora in poi "
                "vengono scartati i campioni più vecchi",
                self._max_samples,
                serie,
            )

    def _throttled_error_log(self, key: str, message: str) -> None:
        now = time.monotonic()
        last = self._error_log_last_at.get(key, 0.0)
        if now - last >= self._error_log_throttle_sec:
            self._error_log_last_at[key] = now
            LOGGER.exception(message)

    def log_pose_pair(
        self,
        raw_pose_estimate: Optional[Mapping[str, Any]],
        filtered_pose_estimate: Optional[Mapping[str, Any]],
        timestamp: Optional[float] = None,
    ) -> bool:
        if not raw_pose_estimate:
            return False

        self._note_eviction_if_full(self.comparison_entries, "comparison")

        monotonic = None
        if timestamp is None:
            timestamp = time.time()
            monotonic = time.monotonic()

        if filtered_pose_estimate is None:
            filtered_pose_estimate = raw_pose_estimate

        try:
            raw_position = _normalize_position_world(raw_pose_estimate["position_world"])
            filtered_position = _normalize_position_world(filtered_pose_estimate["position_world"])

            raw_x, raw_y, raw_z = raw_position.flatten()
            filtered_x, filtered_y, filtered_z = filtered_position.flatten()

            _raw_yaw = raw_pose_estimate.get("yaw_world_deg")
            raw_yaw = float(_raw_yaw) if _raw_yaw is not None else 0.0
            _filt_yaw = filtered_pose_estimate.get("yaw_world_deg")
            filtered_yaw = float(_filt_yaw) if _filt_yaw is not None else raw_yaw

            tag_ids = raw_pose_estimate.get(
                "source_tag_ids",
                filtered_pose_estimate.get("source_tag_ids", []),
            )

            entry = {
                "timestamp": float(timestamp),
                **({} if monotonic is None else {"monotonic": float(monotonic)}),

                "raw_x": float(raw_x),
                "raw_y": float(raw_y),
                "raw_z": float(raw_z),
                "raw_yaw_deg": raw_yaw,

                "filtered_x": float(filtered_x),
                "filtered_y": float(filtered_y),
                "filtered_z": float(filtered_z),
                "filtered_yaw_deg": filtered_yaw,

                "error_x": float(filtered_x - raw_x),
                "error_y": float(filtered_y - raw_y),
                "error_z": float(filtered_z - raw_z),

                "tag_ids": [int(tag_id) for tag_id in (tag_ids or [])],
                "raw_source": str(raw_pose_estimate.get("source", "raw")),
                "filtered_source": str(filtered_pose_estimate.get("source", "filtered")),

                "outlier_rejected": bool(filtered_pose_estimate.get("kalman_outlier_rejected", False)),
            }

            entry["error_norm"] = float(
                np.linalg.norm(
                    [
                        entry["error_x"],
                        entry["error_y"],
                        entry["error_z"],
                    ]
                )
            )

            self.comparison_entries.append(entry)

            return True

        except Exception:
            self._throttled_error_log(
                "log_pose_pair",
                "Non è stato possibile registrare la posizione grezza e quella filtrata",
            )
            return False

    def log_autopilot_step(
        self,
        pose_estimate: Optional[Mapping[str, Any]],
        command: Optional[Mapping[str, Any]],
        target: Optional[Any] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        if command is None:
            return False

        self._note_eviction_if_full(self.autopilot_entries, "autopilot")

        monotonic = None
        if timestamp is None:
            timestamp = time.time()
            monotonic = time.monotonic()

        try:
            if pose_estimate is None:
                x = y = z = None
                yaw_deg = None
            else:
                position = _normalize_position_world(pose_estimate["position_world"])
                x, y, z = position.flatten()
                _yaw = pose_estimate.get("yaw_world_deg")
                yaw_deg = float(_yaw) if _yaw is not None else 0.0

            target_x = self._get_attr_or_key(target, "x", None)
            target_y = self._get_attr_or_key(target, "y", None)
            target_z = self._get_attr_or_key(target, "z", None)
            target_yaw_deg = self._get_attr_or_key(target, "yaw_deg", None)

            if target_x is not None:
                target_x = float(target_x)
            if target_y is not None:
                target_y = float(target_y)
            if target_z is not None:
                target_z = float(target_z)
            if target_yaw_deg is not None:
                target_yaw_deg = float(target_yaw_deg)

            distance_xy = self._get_mapping_value(command, "distance_xy", None)
            distance_3d = self._get_mapping_value(command, "distance_3d", None)
            yaw_error_deg = self._get_mapping_value(command, "yaw_error_deg", None)

            if (
                distance_xy is None
                and x is not None
                and target_x is not None
                and target_y is not None
            ):
                distance_xy = math.hypot(target_x - float(x), target_y - float(y))

            if (
                distance_3d is None
                and x is not None
                and target_x is not None
                and target_y is not None
                and target_z is not None
            ):
                dx = target_x - float(x)
                dy = target_y - float(y)
                dz = target_z - float(z)
                distance_3d = math.sqrt(dx * dx + dy * dy + dz * dz)

            if yaw_error_deg is None and yaw_deg is not None and target_yaw_deg is not None:
                yaw_error_deg = _wrap_angle_deg(target_yaw_deg - yaw_deg)

            tag_ids = (
                [] if pose_estimate is None
                else pose_estimate.get("source_tag_ids", [])
            )

            entry = {
                "timestamp": float(timestamp),
                **({} if monotonic is None else {"monotonic": float(monotonic)}),

                "x": None if x is None else float(x),
                "y": None if y is None else float(y),
                "z": None if z is None else float(z),
                "yaw_deg": None if yaw_deg is None else float(yaw_deg),

                "target_x": None if target_x is None else float(target_x),
                "target_y": None if target_y is None else float(target_y),
                "target_z": None if target_z is None else float(target_z),
                "target_yaw_deg": None if target_yaw_deg is None else float(target_yaw_deg),

                "lr": int(self._get_mapping_value(command, "lr", 0)),
                "fb": int(self._get_mapping_value(command, "fb", 0)),
                "ud": int(self._get_mapping_value(command, "ud", 0)),
                "yaw_cmd": int(self._get_mapping_value(command, "yaw", 0)),

                "distance_xy": None if distance_xy is None else float(distance_xy),
                "distance_3d": None if distance_3d is None else float(distance_3d),
                "yaw_error_deg": None if yaw_error_deg is None else float(yaw_error_deg),

                "target_index": self._get_mapping_value(command, "target_index", None),
                "reached": bool(self._get_mapping_value(command, "reached", False)),
                "finished": bool(self._get_mapping_value(command, "finished", False)),
                "fault": bool(self._get_mapping_value(command, "fault", False)),
                "reason": str(self._get_mapping_value(command, "reason", "")),

                "tag_ids": [int(tag_id) for tag_id in (tag_ids or [])],
                "pose_source": (
                    "" if pose_estimate is None
                    else str(pose_estimate.get("source", "pose"))
                ),
            }

            self.autopilot_entries.append(entry)

            if self.autopilot_xy_tolerance_m is None:
                _xy_tol = self._get_mapping_value(command, "xy_tolerance_m")
                if _xy_tol is not None:
                    self.autopilot_xy_tolerance_m = float(_xy_tol)
            if self.autopilot_z_tolerance_m is None:
                _z_tol = self._get_mapping_value(command, "z_tolerance_m")
                if _z_tol is not None:
                    self.autopilot_z_tolerance_m = float(_z_tol)
            if self.autopilot_yaw_tolerance_deg is None:
                _yaw_tol = self._get_mapping_value(command, "yaw_tolerance_deg")
                if _yaw_tol is not None:
                    self.autopilot_yaw_tolerance_deg = float(_yaw_tol)

            return True

        except Exception:
            self._throttled_error_log(
                "log_autopilot_step",
                "Non è stato possibile registrare il campione dell'autopilota",
            )
            return False

    AUTOPILOT_TEXT_FILENAME = "autopilot_apriltag_mission_log.txt"
    COMPARISON_TEXT_FILENAME = "kalman_raw_vs_filtered_poses.txt"

    EXCEL_FILENAME = "flight_data.xlsx"

    FILTER_DIVERGENCE_MARGIN_M = 0.10

    def save_all_text_files(self, output_dir: str | Path) -> list[Path]:
        if not self.has_data():
            LOGGER.info("Nessun dato di volo da esportare nei file di testo.")
            return []

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[Path] = []

        try:
            if self.autopilot_entries:
                saved_paths.append(
                    flight_text_report.save_autopilot_data_to_file(
                        self, output_dir / self.AUTOPILOT_TEXT_FILENAME
                    )
                )

            if self.comparison_entries:
                saved_paths.append(
                    flight_text_report.save_comparison_data_to_file(
                        self, output_dir / self.COMPARISON_TEXT_FILENAME
                    )
                )

        except Exception:
            LOGGER.exception("Non è stato possibile salvare i file di testo della sessione")

        return saved_paths

    def save_plots(self, output_dir: str | Path) -> list[Path]:
        from drone.ui.plots import flight_plots
        return flight_plots.save_plots(self, output_dir)


    def _save_excel(
        self,
        session_dir: Path,
        *,
        app_config: Any = None,
        path_name: Optional[str] = None,
    ) -> Optional[Path]:
        try:
            from drone.data import flight_excel_report
        except ImportError:
            LOGGER.info(
                "openpyxl non disponibile: la sessione viene salvata nei file di testo. "
                "Per avere il file Excel: pip install openpyxl."
            )
            return None

        try:
            parameters = None
            if app_config is not None:
                parameters = flight_excel_report.collect_run_parameters(
                    self, app_config, path_name=path_name
                )
            return flight_excel_report.save_session_workbook(
                self, session_dir / self.EXCEL_FILENAME, parameters
            )
        except Exception:
            LOGGER.exception(
                "Non è stato possibile scrivere il file Excel della sessione: "
                "vengono salvati i file di testo"
            )
            return None

    def export_session(
        self,
        output_root: Optional[str | Path] = None,
        *,
        app_config: Any = None,
        path_name: Optional[str] = None,
    ) -> Optional[Path]:
        if not self.has_data():
            LOGGER.info("Nessun dato di volo da esportare: la cartella della sessione non viene creata.")
            return None

        try:
            if output_root is None:
                output_root = self.output_dir

            output_root = Path(output_root)

            session_name = time.strftime("flight_session_%d_%m_%Y_%H_%M_%S")
            session_dir = output_root / session_name
            if session_dir.exists():
                suffix = 2
                while (output_root / f"{session_name}_{suffix}").exists():
                    suffix += 1
                session_dir = output_root / f"{session_name}_{suffix}"
            session_dir.mkdir(parents=True, exist_ok=True)

            excel_path = self._save_excel(
                session_dir, app_config=app_config, path_name=path_name
            )
            if excel_path is None:
                self.save_all_text_files(session_dir)

            try:
                self.save_plots(session_dir)
            except Exception:
                LOGGER.exception(
                    "Non è stato possibile generare i grafici: i dati della sessione "
                    "restano comunque salvati"
                )

            return session_dir

        except Exception:
            LOGGER.exception("Non è stato possibile esportare la sessione di volo")
            return None

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        minuti, secondi = divmod(seconds, 60)
        parti = []
        if minuti:
            parti.append(f"{minuti} minuto" if minuti == 1 else f"{minuti} minuti")
        if secondi or not minuti:
            parti.append(f"{secondi} secondo" if secondi == 1 else f"{secondi} secondi")
        return " e ".join(parti)

    def _format_session(self, entries, contesto: str, contenuto: str) -> str:
        start_time = entries[0]["timestamp"]
        end_time = entries[-1]["timestamp"]
        duration = end_time - start_time
        total = len(entries)

        cadenza = ""
        if total > 1 and duration > 0:
            passo = duration / (total - 1)
            cadenza = (
                f", uno ogni {passo * 1000:.0f} millisecondi"
                if passo < 1.0
                else f", uno ogni {passo:.1f} secondi".replace(".", ",")
            )
        registrati = (
            f"è stato registrato 1 campione {contenuto}"
            if total == 1
            else f"sono stati registrati {total} campioni {contenuto}{cadenza}"
        )
        return (
            f"{contesto} {registrati}\n"
            f"Il volo è durato {self._format_duration(duration)}, "
            f"dalle {time.strftime('%H:%M:%S', time.localtime(start_time))} "
            f"alle {time.strftime('%H:%M:%S', time.localtime(end_time))}"
        )

    def get_summary(self) -> str:
        if self.autopilot_entries:
            return self._format_session(
                self.autopilot_entries, "Durante il volo autonomo", "di posizione e di comando",
            )

        if self.comparison_entries:
            return self._format_session(
                self.comparison_entries, "Durante il volo", "della posizione grezza e filtrata",
            )

        return "Non è stato registrato alcun dato"
