from __future__ import annotations

from pathlib import Path
from typing import Optional

from openpyxl import Workbook

from drone.data.flight_excel_sheets import (
    write_autopilot_sheet,
    write_kalman_sheet,
    write_legend_sheet,
    write_parameters_sheet,
    write_summary_sheet,
)
from drone.data.flight_excel_style import fmt_num_it


def collect_run_parameters(logger, app_config, *, path_name: Optional[str] = None) -> list[tuple[str, list[tuple[str, str]]]]:
    ap = app_config.apriltag_autopilot
    pf = app_config.pose_filter
    cp = app_config.camera_pose

    xy_tol = logger.autopilot_xy_tolerance_m if logger.autopilot_xy_tolerance_m is not None else ap.xy_tolerance_m
    z_tol = logger.autopilot_z_tolerance_m if logger.autopilot_z_tolerance_m is not None else ap.z_tolerance_m
    yaw_tol = logger.autopilot_yaw_tolerance_deg if logger.autopilot_yaw_tolerance_deg is not None else ap.yaw_tolerance_deg

    n_waypoints = len({e.get("target_index") for e in logger.autopilot_entries if e.get("target_index") is not None})

    def _n(value, decimals=3):
        return fmt_num_it(float(value), decimals)

    if cp.fusion_mode == "weighted_average":
        fusione = "tutti, contando di più quelli vicini"
    elif cp.fusion_mode == "best_tag":
        fusione = "solo il più affidabile"
    else:
        fusione = str(cp.fusion_mode)
    priorita_z = (
        f"si ferma oltre {_n(ap.z_priority_enter_m, 2)} m di scarto, "
        f"riparte sotto {_n(ap.z_priority_exit_m, 2)} m"
        if ap.z_priority_enabled else "disattivata"
    )
    timeout_wp = (
        f"{_n(ap.waypoint_timeout_sec, 0)} s" if ap.waypoint_timeout_enabled else "disattivato"
    )
    gate = (
        f"soglia {_n(pf.outlier_gate_threshold, 1)}, al massimo "
        f"{pf.outlier_gate_max_consecutive} scarti di fila"
        if pf.outlier_gate_enabled else "disattivato"
    )
    camera = (
        f"calibrata (lunghezza focale {cp.camera_matrix[0][0]:.0f} × "
        f"{cp.camera_matrix[1][1]:.0f} pixel)"
    )
    # Con None la localizzazione usa i tag a qualunque distanza.
    distanza_tag = (
        "nessun limite" if cp.max_tag_distance_m is None else _n(cp.max_tag_distance_m, 1)
    )

    return [
        ("Missione", [
            ("Percorso", path_name if path_name else "—"),
            ("Numero di waypoint", str(n_waypoints)),
            ("Atterraggio automatico a fine missione", "Sì" if ap.auto_land_on_finish else "No"),
        ]),
        ("Guida automatica", [
            ("Reattività negli spostamenti", _n(ap.kp_xy, 1)),
            ("Reattività in salita e discesa", _n(ap.kp_z, 1)),
            ("Reattività nella rotazione", _n(ap.kp_yaw, 1)),
            ("Limite comando avanti/laterale [0–100]", str(ap.max_xy_speed)),
            ("Limite comando verticale [0–100]", str(ap.max_z_speed)),
            ("Limite comando di rotazione [0–100]", str(ap.max_yaw_speed)),
            ("Margine di distanza dal waypoint [m]", _n(xy_tol)),
            ("Margine di altezza [m]", _n(z_tol)),
            ("Margine di direzione [°]", _n(yaw_tol, 1)),
            ("Correzione dell'altezza da fermo", priorita_z),
            ("Attesa se la posizione si perde [s]", _n(ap.pose_timeout_sec, 1)),
            ("Tempo massimo per ogni waypoint", timeout_wp),
        ]),
        ("Correzione della posizione (filtro di Kalman)", [
            ("Reattività del filtro (rumore di processo)", _n(pf.process_noise, 1)),
            ("Incertezza attribuita alla misura [m]", _n(pf.measurement_noise, 2)),
            ("Gestione delle misure anomale (outlier)", gate),
            ("Filtro applicato anche alla direzione", "attivo" if pf.yaw_filter_enabled else "disattivo"),
        ]),
        ("Posizione dai marker (AprilTag)", [
            ("Modello dei marker", str(cp.tag_family)),
            ("Dimensione dei marker [m]", _n(cp.tag_size_m, 2)),
            ("Metodo di fusione dei marker", fusione),
            ("Distanza massima dei marker [m]", distanza_tag),
            ("Calibrazione della telecamera", camera),
        ]),
        ("Sicurezza batteria", [
            ("Rientro alla base [%]", str(app_config.battery_rth_pct)),
            ("Avviso batteria [%]", str(app_config.battery_warning_pct)),
            ("Atterraggio immediato [%]", str(app_config.battery_critical_pct)),
        ]),
    ]

def _build_workbook(logger, parameters: Optional[list[tuple[str, list[tuple[str, str]]]]] = None) -> Workbook:
    separator = logger.TAG_IDS_SEPARATOR
    auto = list(logger.autopilot_entries)
    comp = list(logger.comparison_entries)

    wb = Workbook()
    wb.properties.creator = "Work-Guardian"
    wb.properties.title = "Report sessione di volo"
    wb.properties.subject = "Dati e riepilogo della sessione di volo"
    wb.properties.description = "Report Work-Guardian con riepilogo, parametri, dati autopilota, confronto Kalman e legenda."
    summary_ws = wb.active
    summary_ws.title = "Riepilogo"
    write_summary_sheet(summary_ws, logger)

    if parameters:
        write_parameters_sheet(wb.create_sheet("Parametri"), parameters)

    if auto:
        write_autopilot_sheet(wb.create_sheet("Autopilota"), auto, separator=separator)

    if comp:
        write_kalman_sheet(wb.create_sheet("Kalman"), comp, separator=separator)

    write_legend_sheet(
        wb.create_sheet("Legenda"),
        include_parameters=bool(parameters),
        include_autopilot=bool(auto),
        include_kalman=bool(comp),
    )

    return wb


def save_session_workbook(logger, output_path: Path, parameters=None) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = _build_workbook(logger, parameters)
    wb.save(output_path)
    return output_path
