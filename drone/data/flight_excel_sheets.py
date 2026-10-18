from __future__ import annotations

import time
from typing import Callable, Optional

from openpyxl.worksheet.worksheet import Worksheet

from drone.data.excel_tables import (
    column_notes,
    write_data_sheet,
    write_legend_sheet as write_legend_blocks,
    write_parameters_sheet as write_parameters_block,
    write_stats_table,
)
from drone.data.flight_excel_style import (
    ALERT_FILL,
    ALERT_FONT,
    ALERT_HEADER_FILL,
    BASE_FONT,
    CELL_BORDER,
    CENTER,
    CONTROL_HEADER_FILL,
    FMT_DEG,
    FMT_INT,
    FMT_M,
    FMT_PCT,
    FMT_S,
    FMT_TIME,
    LEFT,
    MEASURE_HEADER_FILL,
    OK_FILL,
    OK_FONT,
    QUALITY_HEADER_FILL,
    RIGHT,
    STATUS_HEADER_FILL,
    TAB_AUTOPILOT,
    TAB_KALMAN,
    TAB_LEGEND,
    TAB_PARAMETERS,
    TARGET_HEADER_FILL,
    TIME_HEADER_FILL,
    WARN_FILL,
    WARN_FONT,
    WRAP_LEFT,
    bool_it,
    clock,
    fmt_num_it,
    kv,
    metric_card,
    prepare_summary_sheet,
    section_title,
    table_header,
    translate_reason,
    translate_source,
)
from drone.data.flight_report_stats import (
    column_stats,
    elapsed_seconds,
    filter_divergence,
    join_tag_ids,
    percent_within,
    rms,
    total_duration_sec,
    values_of,
    waypoint_groups,
)
def _filter_divergence_flags(entries, margin_m: float) -> list[str]:
    return [
        f"Asse {d['axis']}: posizione corretta da {fmt_num_it(d['filtered_min'])} a {fmt_num_it(d['filtered_max'])} m, "
        f"misurata da {fmt_num_it(d['raw_min'])} a {fmt_num_it(d['raw_max'])} m (oltre di {fmt_num_it(d['excess'])} m)"
        for d in filter_divergence(entries, margin_m)
    ]


def _rel_time(entries):
    tempi = {id(e): ti for e, ti in zip(entries, elapsed_seconds(entries))}
    return lambda e: tempi.get(id(e), 0.0)


def _autopilot_columns(entries, separator: str) -> list[tuple[str, Callable, Optional[str]]]:
    rel = _rel_time(entries)

    def wp_number(e):
        idx = e.get("target_index")
        return None if idx is None else int(idx) + 1

    return [
        ("Orario", lambda e: clock(e["timestamp"]), FMT_TIME),
        ("Tempo dall'avvio [s]", rel, FMT_S),
        ("Drone X [m]", lambda e: e["x"], FMT_M),
        ("Drone Y [m]", lambda e: e["y"], FMT_M),
        ("Drone Z [m]", lambda e: e["z"], FMT_M),
        ("Direzione del drone [°]", lambda e: e["yaw_deg"], FMT_DEG),
        ("Waypoint X [m]", lambda e: e.get("target_x"), FMT_M),
        ("Waypoint Y [m]", lambda e: e.get("target_y"), FMT_M),
        ("Waypoint Z [m]", lambda e: e.get("target_z"), FMT_M),
        ("Direzione richiesta [°]", lambda e: e.get("target_yaw_deg"), FMT_DEG),
        ("Comando avanti/indietro", lambda e: e["fb"], FMT_INT),
        ("Comando destra/sinistra", lambda e: e["lr"], FMT_INT),
        ("Comando salita/discesa", lambda e: e["ud"], FMT_INT),
        ("Comando rotazione", lambda e: e["yaw_cmd"], FMT_INT),
        ("Distanza orizzontale [m]", lambda e: e.get("distance_xy"), FMT_M),
        ("Distanza nello spazio [m]", lambda e: e.get("distance_3d"), FMT_M),
        ("Errore di direzione [°]", lambda e: e.get("yaw_error_deg"), FMT_DEG),
        ("Waypoint da raggiungere (n.)", wp_number, FMT_INT),
        ("Waypoint raggiunto", lambda e: bool_it(e.get("reached")), None),
        ("Missione completata", lambda e: bool_it(e.get("finished")), None),
        ("Problema", lambda e: bool_it(e.get("fault")), None),
        ("Cosa sta facendo il drone", lambda e: translate_reason(e.get("reason", "")), None),
        ("Marker visti", lambda e: join_tag_ids(e.get("tag_ids"), separator), None),
        ("Metodo di stima della posizione", lambda e: translate_source(e.get("pose_source", "")), None),
    ]


def _comparison_columns(entries, separator: str) -> list[tuple[str, Callable, Optional[str]]]:
    rel = _rel_time(entries)
    return [
        ("Orario", lambda e: clock(e["timestamp"]), FMT_TIME),
        ("Tempo dall'avvio [s]", rel, FMT_S),
        ("Misurata X [m]", lambda e: e["raw_x"], FMT_M),
        ("Misurata Y [m]", lambda e: e["raw_y"], FMT_M),
        ("Misurata Z [m]", lambda e: e["raw_z"], FMT_M),
        ("Direzione misurata [°]", lambda e: e["raw_yaw_deg"], FMT_DEG),
        ("Corretta X [m]", lambda e: e["filtered_x"], FMT_M),
        ("Corretta Y [m]", lambda e: e["filtered_y"], FMT_M),
        ("Corretta Z [m]", lambda e: e["filtered_z"], FMT_M),
        ("Direzione corretta [°]", lambda e: e["filtered_yaw_deg"], FMT_DEG),
        ("Correzione applicata X [m]", lambda e: e["error_x"], FMT_M),
        ("Correzione applicata Y [m]", lambda e: e["error_y"], FMT_M),
        ("Correzione applicata Z [m]", lambda e: e["error_z"], FMT_M),
        ("Entità della correzione [m]", lambda e: e["error_norm"], FMT_M),
        ("Marker visti", lambda e: join_tag_ids(e.get("tag_ids"), separator), None),
        ("Metodo della posizione misurata", lambda e: translate_source(e.get("raw_source")), None),
        ("Metodo della posizione corretta", lambda e: translate_source(e.get("filtered_source")), None),
        ("Misura scartata", lambda e: bool_it(e.get("outlier_rejected")), None),
    ]


_STAT_LABELS = {
    "x": "Drone X [m]", "y": "Drone Y [m]", "z": "Drone Z [m]", "yaw_deg": "Direzione del drone [°]",
    "distance_xy": "Distanza orizzontale [m]", "distance_3d": "Distanza nello spazio [m]",
    "yaw_error_deg": "Errore di direzione [°]",
    "raw_x": "Misurata X [m]", "raw_y": "Misurata Y [m]", "raw_z": "Misurata Z [m]",
    "raw_yaw_deg": "Direzione misurata [°]",
    "filtered_x": "Corretta X [m]", "filtered_y": "Corretta Y [m]",
    "filtered_z": "Corretta Z [m]", "filtered_yaw_deg": "Direzione corretta [°]",
    "error_norm": "Entità della correzione [m]",
}


def _stat_format(name: str) -> str:
    return FMT_DEG if "yaw" in name else FMT_M


def _write_stats_table(ws: Worksheet, row: int, stats) -> int:
    return write_stats_table(
        ws, row, stats, labels=_STAT_LABELS, number_format=_stat_format
    )


def write_summary_sheet(ws: Worksheet, logger) -> None:
    auto = list(logger.autopilot_entries)
    comp = list(logger.comparison_entries)

    groups = waypoint_groups(auto) if auto else []
    indices = sorted({g["index"] for g in groups if g["index"] is not None})
    reached_indices = {
        g["index"] for g in groups if g["reached"] and g["index"] is not None
    }
    completed = any(e.get("finished") for e in auto) if auto else False
    fault_entry = next((e for e in reversed(auto) if e.get("fault")), None) if auto else None

    row = prepare_summary_sheet(
        ws,
        "Riepilogo sessione di volo",
        f"Generato il {time.strftime('%d/%m/%Y alle %H:%M:%S')}",
    )

    # Tre indicatori immediati: permettono di capire la sessione senza leggere
    # prima tutte le tabelle sottostanti.
    if auto:
        duration = total_duration_sec(auto)
        mission_text = "Completata" if completed else "Non completata"
        mission_fill = OK_FILL if completed else (ALERT_FILL if fault_entry else WARN_FILL)
        metric_card(ws, row, 1, "Missione", mission_text, fill=mission_fill)
        metric_card(
            ws, row, 3, "Waypoint raggiunti",
            f"{len(reached_indices & set(indices))} / {len(indices)}",
        )
        metric_card(ws, row, 5, "Durata volo [s]", duration, number_format="0.0")
    elif comp:
        duration = total_duration_sec(comp)
        metric_card(ws, row, 1, "Tipo sessione", "Localizzazione")
        metric_card(ws, row, 3, "Campioni di localizzazione", len(comp), number_format=FMT_INT)
        metric_card(ws, row, 5, "Durata [s]", duration, number_format="0.0")
    else:
        metric_card(ws, row, 1, "Sessione", "Nessun dato")
    row += 3

    row = section_title(ws, row, "Panoramica", span=6)
    if auto:
        t0, t1 = float(auto[0]["timestamp"]), float(auto[-1]["timestamp"])
        duration = total_duration_sec(auto)
        row = kv(ws, row, "Campioni di telemetria del volo automatico", len(auto), FMT_INT)
        row = kv(ws, row, "Durata del volo automatico [s]", duration, "0.0")
        if duration > 0:
            row = kv(ws, row, "Frequenza di registrazione [campioni/s]", (len(auto) - 1) / duration, "0.0")
        row = kv(
            ws, row, "Orario di volo",
            f"da {time.strftime('%H:%M:%S', time.localtime(t0))} "
            f"a {time.strftime('%H:%M:%S', time.localtime(t1))}",
        )
    if comp:
        row = kv(ws, row, "Campioni di localizzazione confrontati", len(comp), FMT_INT)
        if not auto:
            row = kv(ws, row, "Durata del monitoraggio della posizione [s]", total_duration_sec(comp), "0.0")
    row += 1

    if auto:
        row = section_title(ws, row, "Esito della missione", span=6)
        row = kv(
            ws, row, "Waypoint raggiunti",
            f"{len(reached_indices & set(indices))} su {len(indices)}",
        )
        row = kv(ws, row, "Missione completata", "Sì" if completed else "No")
        if fault_entry is not None:
            row = kv(
                ws, row, "Problema che ha fermato il volo",
                translate_reason(fault_entry.get("reason", "")),
            )
        row += 1

        row = section_title(ws, row, "Precisione del volo automatico", span=6)
        rms_xy = rms(values_of(auto, "distance_xy"))
        rms_3d = rms(values_of(auto, "distance_3d"))
        rms_yaw = rms(values_of(auto, "yaw_error_deg"))
        if rms_xy is not None:
            row = kv(ws, row, "Errore RMS di distanza orizzontale dal waypoint [m]", rms_xy, FMT_M)
        if rms_3d is not None:
            row = kv(ws, row, "Errore RMS di distanza 3D dal waypoint [m]", rms_3d, FMT_M)
        if rms_yaw is not None:
            row = kv(ws, row, "Errore RMS di direzione [°]", rms_yaw, FMT_DEG)

        xy_tol = logger.autopilot_xy_tolerance_m
        z_tol = logger.autopilot_z_tolerance_m
        yaw_tol = logger.autopilot_yaw_tolerance_deg
        if xy_tol is not None:
            pct = percent_within(
                auto,
                lambda e: float(e["distance_xy"]) <= xy_tol,
                lambda e: e.get("distance_xy") is not None,
            )
            if pct is not None:
                row = kv(
                    ws, row, f"Percentuale di campioni entro {fmt_num_it(xy_tol)} m dal waypoint",
                    pct, FMT_PCT,
                )
        if z_tol is not None:
            pct = percent_within(
                auto,
                lambda e: abs(float(e["z"]) - float(e["target_z"])) <= z_tol,
                lambda e: e.get("z") is not None and e.get("target_z") is not None,
            )
            if pct is not None:
                row = kv(
                    ws, row, f"Percentuale di campioni con errore di quota ≤ {fmt_num_it(z_tol)} m",
                    pct, FMT_PCT,
                )
        if yaw_tol is not None:
            pct = percent_within(
                auto,
                lambda e: abs(float(e["yaw_error_deg"])) <= yaw_tol,
                lambda e: e.get("yaw_error_deg") is not None,
            )
            if pct is not None:
                row = kv(
                    ws, row, f"Percentuale di campioni con errore di direzione ≤ {fmt_num_it(yaw_tol, 1)}°",
                    pct, FMT_PCT,
                )
        row += 1

        row = section_title(ws, row, "Risultati per waypoint", span=6)
        row = table_header(
            ws, row, ["Waypoint", "Tempo con waypoint attivo [s]", "Distanza minima dal waypoint [m]", "Raggiunto"]
        )
        for g in groups:
            label = "—" if g["index"] is None else str(int(g["index"]) + 1)
            duration = g["end"] - g["start"]
            wp_cell = ws.cell(row=row, column=1, value=label)
            wp_cell.font = BASE_FONT
            wp_cell.alignment = CENTER
            wp_cell.border = CELL_BORDER
            dur_cell = ws.cell(row=row, column=2, value=duration)
            dur_cell.font = BASE_FONT
            dur_cell.number_format = "0.0"
            dur_cell.alignment = RIGHT
            dur_cell.border = CELL_BORDER
            if g["min_d3"] is None:
                md_cell = ws.cell(row=row, column=3, value="—")
                md_cell.alignment = CENTER
            else:
                md_cell = ws.cell(row=row, column=3, value=g["min_d3"])
                md_cell.number_format = FMT_M
                md_cell.alignment = RIGHT
            md_cell.font = BASE_FONT
            md_cell.border = CELL_BORDER
            reached = bool(g["reached"])
            re_cell = ws.cell(row=row, column=4, value=bool_it(reached))
            re_cell.alignment = CENTER
            re_cell.border = CELL_BORDER
            if reached:
                re_cell.fill = OK_FILL
                re_cell.font = OK_FONT
            else:
                re_cell.fill = ALERT_FILL
                re_cell.font = ALERT_FONT
            row += 1
        row += 1

        row = section_title(ws, row, "Statistiche del volo automatico", span=6)
        row = _write_stats_table(
            ws, row,
            column_stats(
                auto,
                ["x", "y", "z", "yaw_deg", "distance_xy", "distance_3d", "yaw_error_deg"],
            ),
        )
        row += 1

    if comp:
        row = section_title(ws, row, "Statistiche della localizzazione", span=6)
        row = _write_stats_table(
            ws, row,
            column_stats(
                comp,
                [
                    "raw_x", "raw_y", "raw_z", "raw_yaw_deg",
                    "filtered_x", "filtered_y", "filtered_z", "filtered_yaw_deg",
                    "error_norm",
                ],
            ),
        )
        row += 1

        flags = _filter_divergence_flags(comp, logger.FILTER_DIVERGENCE_MARGIN_M)
        if flags:
            row = section_title(
                ws, row, "Attenzione: correzione della posizione poco affidabile", span=6
            )
            note = ws.cell(
                row=row, column=1,
                value=(
                    f"In questo volo la posizione corretta si è allontanata di oltre "
                    f"{fmt_num_it(logger.FILTER_DIVERGENCE_MARGIN_M, 2)} m da quella misurata dalla "
                    "telecamera: la correzione potrebbe aver sbagliato. Conviene controllare "
                    "le impostazioni della correzione della posizione nel foglio «Parametri»."
                ),
            )
            note.font = BASE_FONT
            note.fill = WARN_FILL
            note.alignment = WRAP_LEFT
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
            for col in range(1, 7):
                ws.cell(row=row, column=col).fill = WARN_FILL
            ws.row_dimensions[row].height = 50
            row += 1
            for item in flags:
                c = ws.cell(row=row, column=1, value=f"• {item}")
                c.font = BASE_FONT
                c.alignment = WRAP_LEFT
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
                row += 1


# Cosa contiene ciascun foglio, nell'ordine in cui compaiono nel file.
_LEGEND_SHEETS = {
    "Riepilogo": "Il riassunto del volo: quanto è durato, se il drone ha completato il percorso e quanto è rimasto vicino ai punti da raggiungere.",
    "Parametri": "Le impostazioni con cui ha volato il drone.",
    "Autopilota": "Il volo automatico istante per istante: dove si trovava il drone, dove doveva andare e quali comandi ha ricevuto.",
    "Kalman": "Per tutto il volo, la posizione del drone come la misura la telecamera e la stessa posizione dopo la correzione dei piccoli errori di misura.",
}


# Parole che compaiono nei nomi delle colonne e dei parametri.
_LEGEND_WORDS = [
    ("Waypoint", "Punto del percorso che il drone deve raggiungere."),
    ("RMS", "Indicatore dell'errore complessivo che dà più peso agli scarti grandi; più è basso, maggiore è la precisione."),
    ("Marker (AprilTag)", "Quadrato bianco e nero attaccato nella stanza: guardandolo con la telecamera, il drone capisce dove si trova."),
    ("X, Y, Z", "X e Y dicono dove si trova il drone nella stanza vista dall'alto; Z è l'altezza da terra. Tutto in metri."),
    ("Direzione", "Il verso in cui è girato il drone, in gradi."),
    ("Misurata / corretta", "«Misurata» è la posizione così come la misura la telecamera. «Corretta» è la stessa posizione dopo un calcolo che toglie i piccoli errori di misura (il filtro di Kalman, da cui prende il nome il foglio «Kalman»)."),
]


# Ogni voce del riepilogo dice quando compare: col volo autonomo
# («autopilota»), con le pose a confronto («kalman»), solo con queste ultime
# («solo_kalman») o con uno qualunque dei due («sempre»).
_LEGEND_SUMMARY = [
    ("autopilota", "Campioni di telemetria del volo automatico", "Numero totale di istanti del volo automatico per i quali è stata salvata la telemetria del drone."),
    ("autopilota", "Durata del volo automatico [s]", "Quanti secondi è durato il volo automatico."),
    ("autopilota", "Frequenza di registrazione [campioni/s]", "Numero medio di campioni di telemetria registrati ogni secondo durante il volo automatico."),
    ("autopilota", "Orario di volo", "Ora di inizio e di fine del volo automatico."),
    ("kalman", "Campioni di localizzazione confrontati", "Numero di istanti in cui sono disponibili sia la posizione misurata dalla telecamera sia quella corretta dal filtro."),
    ("solo_kalman", "Durata del monitoraggio della posizione [s]", "Intervallo di tempo coperto dai campioni di localizzazione nel foglio «Kalman»."),
    ("autopilota", "Waypoint raggiunti", "Quanti punti del percorso il drone ha raggiunto, sul totale."),
    ("autopilota", "Missione completata", "Sì se il drone ha raggiunto tutti i punti del percorso."),
    ("autopilota", "Problema che ha fermato il volo", "C'è solo se il volo automatico si è fermato per un problema, e dice quale."),
    ("autopilota", "Errore RMS di distanza orizzontale dal waypoint [m]", "Errore RMS della distanza orizzontale dal waypoint: dà più peso agli scarti grandi ed è utile per valutare la precisione complessiva del volo."),
    ("autopilota", "Errore RMS di distanza 3D dal waypoint [m]", "Errore RMS della distanza nello spazio dal waypoint, contando anche l'altezza."),
    ("autopilota", "Errore RMS di direzione [°]", "Errore RMS della direzione del drone rispetto a quella richiesta."),
    ("autopilota", "Percentuale di campioni entro … m dal waypoint", "Percentuale dei campioni utilizzabili in cui la distanza orizzontale dal waypoint è entro la tolleranza configurata."),
    ("autopilota", "Percentuale di campioni con errore di quota ≤ … m", "Percentuale dei campioni utilizzabili in cui l'errore di quota è entro la tolleranza configurata."),
    ("autopilota", "Percentuale di campioni con errore di direzione ≤ …°", "Percentuale dei campioni utilizzabili in cui l'errore di direzione è entro la tolleranza configurata."),
    ("autopilota", "Risultati per waypoint", "Riepilogo di ogni waypoint nell'ordine della missione: tempo in cui è rimasto attivo, distanza minima raggiunta ed esito."),
    ("autopilota", "Waypoint", "Numero del punto del percorso (il primo è 1)."),
    ("autopilota", "Tempo con waypoint attivo [s]", "Tempo durante il quale quel waypoint è rimasto il target attivo del controllo."),
    ("autopilota", "Distanza minima dal waypoint [m]", "Minima distanza tridimensionale raggiunta dal drone rispetto al waypoint."),
    ("autopilota", "Raggiunto", "Sì (verde) se il drone ha raggiunto il punto, No (rosso) se non ci è riuscito."),
    ("sempre", "Grandezza", "Parametro per cui vengono riportate le statistiche della sessione."),
    ("sempre", "Minimo / Massimo", "Il valore più basso e quello più alto della colonna."),
    ("sempre", "Media", "Il valore medio della colonna."),
    ("sempre", "Deviazione standard", "Misura quanto i valori sono dispersi attorno alla media: più è bassa, più il valore è stabile. Per le direzioni viene usata la deviazione standard circolare."),
    ("kalman", "Attenzione: correzione della posizione poco affidabile", "C'è solo se la posizione corretta si è allontanata troppo da quella misurata: in quel volo la correzione può aver sbagliato."),
]


_LEGEND_PARAMETERS = [
    ("Percorso", "Il nome del percorso seguito."),
    ("Numero di waypoint", "Quanti punti diversi del percorso il drone ha cercato di raggiungere."),
    ("Atterraggio automatico a fine missione", "Sì se il drone atterra da solo alla fine del percorso."),
    ("Reattività negli spostamenti", "Quanto deciso è il drone nel muoversi verso il punto: più il numero è alto, più si muove forte quando è lontano."),
    ("Reattività in salita e discesa", "Lo stesso, per salire e scendere."),
    ("Reattività nella rotazione", "Lo stesso, per girarsi."),
    ("Limite comando avanti/laterale [0–100]", "Massimo valore assoluto consentito ai comandi RC di avanzamento e movimento laterale."),
    ("Limite comando verticale [0–100]", "Massimo valore assoluto consentito al comando RC di salita e discesa."),
    ("Limite comando di rotazione [0–100]", "Massimo valore assoluto consentito al comando RC di rotazione."),
    ("Margine di distanza dal waypoint [m]", "Quanto vicino al punto deve arrivare il drone, senza contare l'altezza, perché il punto conti come raggiunto."),
    ("Margine di altezza [m]", "Di quanto può sbagliare l'altezza perché il punto conti come raggiunto."),
    ("Margine di direzione [°]", "Di quanti gradi può essere girato male perché il punto conti come raggiunto. Il punto è raggiunto solo se vanno bene tutte e tre: distanza, altezza e direzione."),
    ("Correzione dell'altezza da fermo", "Se il drone è troppo in alto o troppo in basso, si ferma e prima sistema l'altezza; riparte quando lo scarto è tornato piccolo. I due valori dicono quando si ferma e quando riparte."),
    ("Attesa se la posizione si perde [s]", "Se il drone non sa più dove si trova, aspetta fermo per questo tempo; poi il volo automatico si ferma."),
    ("Tempo massimo per ogni waypoint", "Se il drone non raggiunge un punto entro questo tempo, il volo automatico si ferma."),
    ("Reattività del filtro (rumore di processo)", "Parametro del filtro di Kalman che determina quanto rapidamente la stima può adattarsi al movimento del drone."),
    ("Incertezza attribuita alla misura [m]", "Quanto il filtro considera incerta la posizione misurata dalla telecamera: valori maggiori fanno pesare meno la misura istantanea."),
    ("Gestione delle misure anomale (outlier)", "Regole con cui vengono rifiutate misure di posizione incompatibili con il moto atteso del drone; dopo troppi scarti consecutivi il filtro si riallinea alla nuova misura."),
    ("Filtro applicato anche alla direzione", "Indica se il filtro di Kalman viene applicato anche alla direzione del drone oltre che alle coordinate di posizione."),
    ("Modello dei marker", "Il tipo di marker AprilTag usato nella stanza."),
    ("Dimensione dei marker [m]", "Quanto è grande un marker: serve a capire quanto è lontano."),
    ("Metodo di fusione dei marker", "Indica come vengono combinati più marker visibili: tutti con pesi diversi oppure soltanto quello ritenuto più affidabile."),
    ("Distanza massima dei marker [m]", "I marker più lontani di così non si usano, perché meno precisi."),
    ("Calibrazione della telecamera", "Stato della calibrazione usata per trasformare le immagini della telecamera in stime di posizione e distanza."),
    ("Rientro alla base [%]", "Con la batteria a questo livello il drone torna da solo al punto di partenza."),
    ("Avviso batteria [%]", "Con la batteria a questo livello compare l'avviso di batteria quasi scarica."),
    ("Atterraggio immediato [%]", "Con la batteria a questo livello il drone atterra subito, dove si trova."),
]


_LEGEND_AUTOPILOT = [
    ("Orario", "Ora del computer in cui è stata registrata la riga."),
    ("Tempo dall'avvio [s]", "Secondi passati dall'inizio del volo automatico."),
    ("Drone X / Y / Z [m]", "Dove si trovava il drone nella stanza, in metri: X e Y la posizione vista dall'alto, Z l'altezza da terra. Vuota se in quel momento il drone non sapeva dove si trovava."),
    ("Direzione del drone [°]", "Il verso in cui era girato il drone, in gradi."),
    ("Waypoint X / Y / Z [m]", "Dove si trova il punto che il drone deve raggiungere."),
    ("Direzione richiesta [°]", "Il verso in cui il drone deve essere girato quando arriva al punto."),
    ("Comando avanti/indietro", "Comando dato al drone: positivo va avanti, negativo va indietro, 0 sta fermo. Va da −100 a 100."),
    ("Comando destra/sinistra", "Comando dato al drone: positivo va a destra, negativo va a sinistra, 0 sta fermo."),
    ("Comando salita/discesa", "Comando dato al drone: positivo sale, negativo scende, 0 sta fermo."),
    ("Comando rotazione", "Comando dato al drone: positivo gira in senso orario, negativo in senso antiorario, 0 non gira."),
    ("Distanza orizzontale [m]", "Distanza dal punto da raggiungere, senza contare l'altezza."),
    ("Distanza nello spazio [m]", "Distanza dal punto da raggiungere, contando anche l'altezza."),
    ("Errore di direzione [°]", "Di quanti gradi il drone deve ancora girarsi per avere la direzione giusta; il segno dice da che parte."),
    ("Waypoint da raggiungere (n.)", "Numero del punto che il drone sta cercando di raggiungere (il primo è 1)."),
    ("Waypoint raggiunto", "Sì (verde) nell'istante in cui il drone ha raggiunto il punto."),
    ("Missione completata", "Sì quando il drone ha raggiunto tutti i punti."),
    ("Problema", "Sì (rosso) quando il volo automatico si ferma per un problema."),
    ("Cosa sta facendo il drone", "Cosa stava facendo il drone in quel momento. Il significato di ogni valore è nella Legenda."),
    ("Marker visti", "I numeri dei marker che il drone vedeva in quel momento."),
    ("Metodo di stima della posizione", "Metodo usato in quell'istante per ottenere la posizione del drone. Il significato dei possibili valori è riportato nella Legenda."),
]


_LEGEND_CONTROL_STATES = [
    ("Inseguimento waypoint", "Il drone si sta muovendo verso il punto."),
    ("Correzione di quota", "Il drone è fermo e sistema l'altezza prima di proseguire."),
    ("Waypoint raggiunto", "Il drone ha raggiunto il punto e passa al successivo."),
    ("Supervisione avviata", "Il drone è arrivato in un punto di controllo e si ferma a osservare."),
    ("Sosta di supervisione", "Il drone è fermo nel punto di controllo e osserva."),
    ("Supervisione: assestamento", "Durante la sosta il drone si è spostato un po' e torna al suo posto."),
    ("Supervisione: quota", "Durante la sosta il drone sistema l'altezza."),
    ("Supervisione completata", "La sosta è finita: il drone passa al punto successivo."),
    ("Missione completata", "Il drone ha raggiunto tutti i punti ed è fermo."),
    ("Posizione assente", "Per un momento il drone non sa dove si trova (non vede i marker) e aspetta fermo."),
    ("Posizione non valida (temporanea)", "Per un momento la posizione calcolata non è utilizzabile e il drone aspetta fermo."),
    ("Posizione persa", "Il drone non sa dove si trova da troppo tempo: il volo automatico si ferma."),
    ("Posizione non valida", "La posizione non è utilizzabile da troppo tempo: il volo automatico si ferma."),
    ("Posizione fuori dalla stanza", "La posizione calcolata è oltre i muri della stanza, quindi è sbagliata: il volo automatico si ferma."),
    ("Tempo scaduto sul waypoint", "Il drone non ha raggiunto il punto entro il tempo massimo: il volo automatico si ferma."),
]


_LEGEND_KALMAN = [
    ("Orario", "Ora del computer in cui è stata calcolata la posizione."),
    ("Tempo dall'avvio [s]", "Secondi passati dal decollo. Attenzione: nel foglio «Autopilota» i secondi partono invece dall'inizio del volo automatico."),
    ("Misurata X / Y / Z [m]", "Posizione del drone come la misura la telecamera guardando i marker, senza correzioni."),
    ("Direzione misurata [°]", "Direzione del drone come la misura la telecamera."),
    ("Corretta X / Y / Z [m]", "Posizione dopo la correzione dei piccoli errori di misura: è quella usata per guidare il drone."),
    ("Direzione corretta [°]", "Direzione del drone dopo la correzione."),
    ("Correzione applicata X / Y / Z [m]", "Differenza tra posizione corretta e posizione misurata, asse per asse."),
    ("Entità della correzione [m]", "Modulo tridimensionale della correzione applicata dal filtro alla posizione misurata."),
    ("Marker visti", "I numeri dei marker usati per calcolare la posizione."),
    ("Metodo della posizione misurata", "Metodo usato per ricavare la posizione direttamente dai marker rilevati."),
    ("Metodo della posizione corretta", "Metodo usato per ottenere la posizione finale dopo l'eventuale filtraggio."),
    ("Misura scartata", "Sì (giallo) quando la misura è stata buttata via perché chiaramente sbagliata: in quell'istante si tiene la posizione di prima."),
]


_LEGEND_POSE_SOURCES = [
    ("Kalman (media pesata dei tag)", "Posizione corretta, calcolata da tutti i marker visti."),
    ("Kalman (tag migliore)", "Posizione corretta, calcolata solo dal marker più affidabile."),
    ("Media pesata dei tag", "Posizione misurata usando tutti i marker visti, contando di più quelli vicini."),
    ("Tag migliore", "Posizione misurata solo dal marker più affidabile."),
]


def _summary_legend(*, include_autopilot: bool, include_kalman: bool) -> list[tuple[str, str]]:
    presenti = set()
    if include_autopilot or include_kalman:
        presenti.add("sempre")
    if include_autopilot:
        presenti.add("autopilota")
    if include_kalman:
        presenti.add("kalman")
        if not include_autopilot:
            presenti.add("solo_kalman")
    return [(nome, significato) for quando, nome, significato in _LEGEND_SUMMARY if quando in presenti]


def write_legend_sheet(
    ws: Worksheet,
    *,
    include_parameters: bool = False,
    include_autopilot: bool,
    include_kalman: bool,
) -> None:
    fogli = ["Riepilogo"]
    if include_parameters:
        fogli.append("Parametri")
    if include_autopilot:
        fogli.append("Autopilota")
    if include_kalman:
        fogli.append("Kalman")

    blocks = [
        ("Fogli del file", [(f"«{nome}»", _LEGEND_SHEETS[nome]) for nome in fogli]),
        ("Parole usate nel file", _LEGEND_WORDS),
    ]
    riepilogo = _summary_legend(include_autopilot=include_autopilot, include_kalman=include_kalman)
    if riepilogo:
        blocks.append(("Foglio «Riepilogo»", riepilogo))
    if include_parameters:
        blocks.append(("Foglio «Parametri»", _LEGEND_PARAMETERS))
    if include_autopilot:
        blocks.append(("Foglio «Autopilota»", _LEGEND_AUTOPILOT))
        blocks.append(("Cosa vuol dire ogni valore di «Cosa sta facendo il drone»", _LEGEND_CONTROL_STATES))
    if include_kalman:
        blocks.append(("Foglio «Kalman»", _LEGEND_KALMAN))
    if include_autopilot or include_kalman:
        blocks.append(("Cosa vuol dire ogni metodo di stima della posizione", _LEGEND_POSE_SOURCES))
    write_legend_blocks(ws, blocks, name_width=44, tab_color=TAB_LEGEND)


def write_parameters_sheet(ws: Worksheet, parameters: list[tuple[str, list[tuple[str, str]]]]) -> None:
    write_parameters_block(
        ws,
        parameters,
        subtitle="Le impostazioni con cui ha volato il drone.",
        note=(
            "I limiti dei comandi sono espressi nella scala RC del drone, da −100 a 100; "
            "il valore indicato è il massimo consentito in valore assoluto."
        ),
        tab_color=TAB_PARAMETERS,
    )


def write_autopilot_sheet(ws: Worksheet, entries, *, separator: str) -> None:
    time_cols = {"Orario", "Tempo dall'avvio [s]"}
    drone_cols = {"Drone X [m]", "Drone Y [m]", "Drone Z [m]", "Direzione del drone [°]"}
    target_cols = {
        "Waypoint X [m]", "Waypoint Y [m]", "Waypoint Z [m]",
        "Direzione richiesta [°]", "Waypoint da raggiungere (n.)",
        "Waypoint raggiunto", "Missione completata",
    }
    control_cols = {
        "Comando avanti/indietro", "Comando destra/sinistra",
        "Comando salita/discesa", "Comando rotazione",
    }
    quality_cols = {
        "Distanza orizzontale [m]", "Distanza nello spazio [m]",
        "Errore di direzione [°]",
    }
    status_cols = {"Cosa sta facendo il drone", "Marker visti", "Metodo di stima della posizione"}
    header_fills = {}
    header_fills.update({h: TIME_HEADER_FILL for h in time_cols})
    header_fills.update({h: MEASURE_HEADER_FILL for h in drone_cols})
    header_fills.update({h: TARGET_HEADER_FILL for h in target_cols})
    header_fills.update({h: CONTROL_HEADER_FILL for h in control_cols})
    header_fills.update({h: QUALITY_HEADER_FILL for h in quality_cols})
    header_fills.update({h: STATUS_HEADER_FILL for h in status_cols})
    header_fills["Problema"] = ALERT_HEADER_FILL

    write_data_sheet(
        ws,
        entries,
        _autopilot_columns(entries, separator),
        highlights={
            "Problema": ("Sì", ALERT_FILL, ALERT_FONT),
            "Waypoint raggiunto": ("Sì", OK_FILL, OK_FONT),
        },
        notes=column_notes(_LEGEND_AUTOPILOT),
        title="Volo automatico — dati nel tempo",
        subtitle=(
            "Un campione per ogni istante registrato: posizione del drone, waypoint, "
            "comandi, errori e stato della missione."
        ),
        tab_color=TAB_AUTOPILOT,
        header_fills=header_fills,
        freeze_columns=2,
        center_headers={
            "Waypoint raggiunto", "Missione completata", "Problema",
            "Waypoint da raggiungere (n.)",
        },
        table_name="Tbl_Autopilota",
    )


def write_kalman_sheet(ws: Worksheet, entries, *, separator: str) -> None:
    time_cols = {"Orario", "Tempo dall'avvio [s]"}
    measured_cols = {
        "Misurata X [m]", "Misurata Y [m]", "Misurata Z [m]",
        "Direzione misurata [°]",
    }
    corrected_cols = {
        "Corretta X [m]", "Corretta Y [m]", "Corretta Z [m]",
        "Direzione corretta [°]",
    }
    correction_cols = {
        "Correzione applicata X [m]", "Correzione applicata Y [m]", "Correzione applicata Z [m]",
        "Entità della correzione [m]",
    }
    diagnostic_cols = {
        "Marker visti", "Metodo della posizione misurata",
        "Metodo della posizione corretta",
    }
    header_fills = {}
    header_fills.update({h: TIME_HEADER_FILL for h in time_cols})
    header_fills.update({h: MEASURE_HEADER_FILL for h in measured_cols})
    header_fills.update({h: TARGET_HEADER_FILL for h in corrected_cols})
    header_fills.update({h: CONTROL_HEADER_FILL for h in correction_cols})
    header_fills.update({h: STATUS_HEADER_FILL for h in diagnostic_cols})
    header_fills["Misura scartata"] = QUALITY_HEADER_FILL

    write_data_sheet(
        ws,
        entries,
        _comparison_columns(entries, separator),
        highlights={
            "Misura scartata": ("Sì", WARN_FILL, WARN_FONT),
        },
        notes=column_notes(_LEGEND_KALMAN),
        title="Posizione misurata e corretta",
        subtitle=(
            "Confronto istante per istante tra la posa misurata dalla telecamera e "
            "quella corretta dal filtro di Kalman."
        ),
        tab_color=TAB_KALMAN,
        header_fills=header_fills,
        freeze_columns=2,
        center_headers={"Misura scartata"},
        table_name="Tbl_Kalman",
    )
