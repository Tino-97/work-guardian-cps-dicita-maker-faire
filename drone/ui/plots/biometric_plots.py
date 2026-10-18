from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from drone.config import APP_CONFIG
from drone.data.biometric_report_stats import (
    MAX_GAP_SEC as _MAX_GAP_SEC,
    NOMINAL_STEP_SEC as _NOMINAL_STEP_SEC,
    STATE_LABELS as _STATE_LABELS,
    alarm_causes,
    format_duration as _format_duration,
)
from drone.data.flight_report_stats import duration_key
from drone.ui.plots.axes import (
    SAT_DASHES,
    add_session_subtitle,
    add_wp_change_markers,
    draw_each,
    figure_saver,
    ordered_legend,
)
from drone.ui.plots.palette import PALETTE

LOGGER = logging.getLogger(__name__)

_SAVE_DPI = 300

_FILENAMES = {
    "hr_comparison":   "biometric_raw_and_filtered_heart_rate.png",
    "spo2_comparison": "biometric_raw_and_filtered_spo2.png",
    "hr_session":      "biometric_heart_rate_trend.png",
    "spo2_session":    "biometric_spo2_trend.png",
}

# Il colore di ogni stato dell'automa del firmware; le etichette e l'ordine in
# cui compaiono in legenda stanno in biometric_report_stats, gli stessi che
# usano i report della sessione.
_STATE_COLORS = {
    "MISSIONE_NON_AVVIATA": PALETTE["stato_non_avviata"],
    "RICERCA_SEGNALE":      PALETTE["stato_ricerca"],
    "NORMALE":              PALETTE["stato_normale"],
    "VERIFICA":             PALETTE["stato_verifica"],
    "ALLARME":              PALETTE["stato_allarme"],
    "SILENZIATO":           PALETTE["stato_silenziato"],
    "GUASTO":               PALETTE["stato_guasto"],
}


@dataclass
class _Data:
    plt: object
    save_figure: Callable
    t: np.ndarray
    hr_raw: np.ndarray
    hr_filtered: np.ndarray
    spo2_raw: np.ndarray
    spo2_filtered: np.ndarray
    bpm: np.ndarray
    spo2: np.ndarray
    states: list
    step: float
    hr_alarm_times: np.ndarray
    spo2_alarm_times: np.ndarray
    duration: float
    meta: str


def _series(samples, key: str) -> np.ndarray:
    return np.array(
        [np.nan if s.get(key) is None else float(s[key]) for s in samples],
        dtype=np.float64,
    )


def _break_gaps(t: np.ndarray, columns: list, states: list):
    # Dove manca un tratto di telemetria si inserisce un punto vuoto: NaN
    # spezza le linee, None lascia bianca la striscia degli stati.
    cuts = np.flatnonzero(np.diff(t) > _MAX_GAP_SEC)
    if not cuts.size:
        return t, columns, states
    positions = cuts + 1
    midpoints = (t[cuts] + t[positions]) / 2.0
    t = np.insert(t, positions, midpoints)
    columns = [np.insert(col, positions, np.nan) for col in columns]
    states = list(states)
    for pos in reversed(positions.tolist()):
        states.insert(pos, None)
    return t, columns, states


def _alarm_causes(event) -> tuple[bool, bool]:
    # Senza valori leggibili l'allarme resta su entrambi i grafici.
    return alarm_causes(event, APP_CONFIG.smartwatch_thresholds)


def _alarm_labels(prefix: str, count: int) -> list[str]:
    # Un allarme solo porta il nome del parametro; con due o più ciascuno ha
    # accanto il suo numero d'ordine.
    if count == 1:
        return [prefix]
    return [f"{prefix} {i}" for i in range(1, count + 1)]


def _alarm_legend_label(parameter: str, labels: list[str]) -> str:
    if len(labels) == 1:
        return f"Allarme {parameter} ({labels[0]})"
    shown = ", ".join(labels) if len(labels) <= 3 else f"{labels[0]}, {labels[1]}, …"
    return f"Allarmi {parameter} ({shown})"


def _prepare(logger, output_dir: Path, plt, saved_paths: list) -> _Data:
    samples = logger.samples()
    events = logger.events()

    key = duration_key(list(samples) + list(events))
    base = float(samples[0][key])
    t = np.array([float(s[key]) - base for s in samples], dtype=np.float64)

    steps = np.diff(t)
    steps = steps[(steps > 0) & (steps <= _MAX_GAP_SEC)]
    step = float(np.median(steps)) if steps.size else _NOMINAL_STEP_SEC

    duration = float(t[-1])
    meta = f"{len(samples)} campioni · {duration:.0f} s"

    columns = [
        _series(samples, "hr_grezzo"),
        _series(samples, "hr_filtrato"),
        _series(samples, "spo2_grezzo"),
        _series(samples, "spo2_filtrato"),
        _series(samples, "bpm"),
        _series(samples, "spo2"),
    ]
    states = [s.get("stato") for s in samples]
    t, columns, states = _break_gaps(t, columns, states)
    hr_raw, hr_filtered, spo2_raw, spo2_filtered, bpm, spo2 = columns

    hr_alarm_times, spo2_alarm_times = [], []
    for event in events:
        if key not in event:
            continue
        hr_cause, spo2_cause = _alarm_causes(event)
        when = float(event[key]) - base
        if hr_cause:
            hr_alarm_times.append(when)
        if spo2_cause:
            spo2_alarm_times.append(when)

    return _Data(
        plt=plt,
        save_figure=figure_saver(output_dir, _SAVE_DPI, saved_paths, plt),
        t=t,
        hr_raw=hr_raw, hr_filtered=hr_filtered,
        spo2_raw=spo2_raw, spo2_filtered=spo2_filtered,
        bpm=bpm, spo2=spo2,
        states=states, step=step,
        hr_alarm_times=np.array(hr_alarm_times, dtype=np.float64),
        spo2_alarm_times=np.array(spo2_alarm_times, dtype=np.float64),
        duration=duration,
        meta=meta,
    )


def _style_panel(ax, ylabel: str) -> None:
    ax.set_ylabel(ylabel, fontsize=10.5)
    ax.grid(True, linestyle="-", linewidth=0.5, alpha=0.40, color=PALETTE["griglia"])
    ax.minorticks_on()
    ax.grid(True, which="minor", linestyle=":", linewidth=0.3,
            alpha=0.20, color=PALETTE["griglia"])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=9)


def _subtitle(data: _Data) -> str:
    return f"Sessione biometrica  ·  {data.meta}"


def _mean_gap(raw: np.ndarray, filtered: np.ndarray) -> Optional[float]:
    both = np.isfinite(raw) & np.isfinite(filtered)
    if not np.any(both):
        return None
    return float(np.mean(np.abs(raw[both] - filtered[both])))


def _comparison(data: _Data, *, raw, filtered, ylabel, unit, title, filename) -> None:
    plt, t = data.plt, data.t

    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    if np.any(np.isfinite(raw)):
        ax.plot(t, raw, linewidth=2.4, alpha=0.5,
                color=PALETTE["grezza"], label="Valore grezzo", zorder=2)
    else:
        ax.text(0.5, 0.92, "Valori grezzi non ricevuti dall'orologio",
                transform=ax.transAxes, ha="center", va="top",
                fontsize=9, color=PALETTE["sottotitolo"])
    if np.any(np.isfinite(filtered)):
        ax.plot(t, filtered, linewidth=1.4,
                color=PALETTE["filtrata"], label="Valore filtrato", zorder=3)

    gap = _mean_gap(raw, filtered)
    if gap is not None:
        ax.text(
            0.995, 0.97, f"scarto medio grezzo − filtrato  {gap:.1f} {unit}",
            transform=ax.transAxes, ha="right", va="top", fontsize=8.5,
            color=PALETTE["media"], zorder=6,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor=PALETTE["griglia"], alpha=0.9),
        )
    _style_panel(ax, ylabel)
    ax.set_xlabel("Tempo [s]", fontsize=11)

    handles = [
        Line2D([0], [0], color=PALETTE["filtrata"], linewidth=1.4,
               label="Valore filtrato (mediana + media mobile esponenziale)"),
        Line2D([0], [0], color=PALETTE["grezza"], linewidth=2.4, alpha=0.5,
               label="Valore grezzo (sensore MAX30100)"),
    ]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16),
              ncol=2, fontsize=9, frameon=True, framealpha=0.95)

    ax.set_title(title, fontsize=13, fontweight="bold", pad=26)
    add_session_subtitle(ax, _subtitle(data))
    data.save_figure(fig, filename)


def _hr_comparison(data: _Data) -> None:
    _comparison(
        data, raw=data.hr_raw, filtered=data.hr_filtered,
        ylabel="Battito [bpm]", unit="bpm",
        title="Battito cardiaco: grezzo vs filtrato",
        filename=_FILENAMES["hr_comparison"],
    )


def _spo2_comparison(data: _Data) -> None:
    _comparison(
        data, raw=data.spo2_raw, filtered=data.spo2_filtered,
        ylabel="SpO2 [%]", unit="%",
        title="Saturazione (SpO2): grezza vs filtrata",
        filename=_FILENAMES["spo2_comparison"],
    )


def _state_segments(data: _Data) -> list[tuple[str, float, float]]:
    # Ogni campione vale fino al successivo; l'ultimo prima di un buco o della
    # fine della sessione vale un passo di pubblicazione.
    t, states = data.t, data.states
    segments: list[tuple[str, float, float]] = []
    for i, state in enumerate(states):
        if state is None:
            continue
        has_next = i + 1 < len(states) and states[i + 1] is not None
        end = float(t[i + 1]) if has_next else float(t[i]) + data.step
        start = float(t[i])
        if segments and segments[-1][0] == state and abs(segments[-1][2] - start) < 1e-9:
            segments[-1] = (state, segments[-1][1], end)
        else:
            segments.append((state, start, end))
    return segments


def _threshold_panel(ax, t, values, *, band, band_label, exit_lines, exit_label,
                     out_mask, unit, ylabel, ylim):
    ax.axhspan(band[0], band[1], color=PALETTE["tolleranza"], alpha=0.12, zorder=1,
               label=band_label)
    for y in exit_lines:
        ax.axhline(y, linewidth=1.1, color=PALETTE["saturazione"],
                   linestyle=SAT_DASHES, alpha=0.8, zorder=2)
    if exit_lines:
        ax.plot([], [], linewidth=1.1, color=PALETTE["saturazione"],
                linestyle=SAT_DASHES, alpha=0.8, label=exit_label)

    finite = np.isfinite(values)
    if np.any(finite):
        ax.plot(t, values, linewidth=1.6, color=PALETTE["filtrata"],
                label="Valore filtrato", zorder=3)
        mean_value = float(np.mean(values[finite]))
        ax.axhline(mean_value, linestyle=":", linewidth=1.2, color=PALETTE["media"],
                   alpha=0.9, zorder=4, label=f"Media  {mean_value:.0f} {unit}")
        if np.any(out_mask):
            ax.scatter(t[out_mask], values[out_mask], s=14, color=PALETTE["arrivo"],
                       zorder=5, label="Oltre la soglia di allarme")
    else:
        ax.text(0.5, 0.5, "Nessuna lettura valida", transform=ax.transAxes,
                ha="center", va="center", fontsize=10, color=PALETTE["sottotitolo"])

    ax.set_ylim(*ylim)
    _style_panel(ax, ylabel)
    ordered_legend(
        ax,
        {"Valore filtrato": 0},
        loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8.5,
        frameon=True, framealpha=0.95,
    )


def _padded_limits(values: np.ndarray, low: float, high: float, pad: float,
                   cap: Optional[float] = None) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    lo = min(low, float(finite.min())) if finite.size else low
    hi = max(high, float(finite.max())) if finite.size else high
    top = hi + pad if cap is None else min(cap, hi + pad)
    return lo - pad, top


def _state_strip(ax, data: _Data) -> list:
    # La striscia degli stati dell'orologio, in fondo a ogni grafico di
    # andamento: ritorna le voci di legenda, con quanto è durato ogni stato.
    durations: dict[str, float] = {}
    for state, start, end in _state_segments(data):
        ax.broken_barh([(start, end - start)], (0, 1),
                       facecolors=_STATE_COLORS.get(state, PALETTE["percorso"]),
                       edgecolor="none")
        durations[state] = durations.get(state, 0.0) + (end - start)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_ylabel("Stato", fontsize=10.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(labelsize=9)
    ax.set_xlabel("Tempo [s]", fontsize=11)

    known = list(_STATE_LABELS)
    present = [code for code in known if code in durations]
    present += sorted(code for code in durations if code not in known)
    return [
        Patch(facecolor=_STATE_COLORS.get(code, PALETTE["percorso"]),
              label=f"{_STATE_LABELS.get(code, code)}  ·  {_format_duration(durations[code])}")
        for code in present
    ]


def _session(data: _Data, *, panel: dict, alarm_times: np.ndarray, alarm_prefix: str,
             alarm_subject: str, title: str, filename: str) -> None:
    plt = data.plt

    fig, (ax_value, ax_state) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, constrained_layout=True,
        gridspec_kw={"height_ratios": [3, 1.1]},
    )
    _threshold_panel(ax_value, data.t, **panel)
    state_handles = _state_strip(ax_state, data)

    n_alarms = len(alarm_times)
    alarm_labels = _alarm_labels(alarm_prefix, n_alarms)
    meta_offset = add_wp_change_markers(
        [ax_value, ax_state], alarm_times, alarm_labels, max(data.duration, 1.0),
        PALETTE["allarme_biometrico"],
    )
    if n_alarms:
        state_handles.append(
            Line2D([0], [0], color=PALETTE["allarme_biometrico"], linestyle="--",
                   linewidth=1.1, label=_alarm_legend_label(alarm_subject, alarm_labels))
        )
    if state_handles:
        ax_state.legend(handles=state_handles, loc="upper left",
                        bbox_to_anchor=(1.01, 1.0), fontsize=8.5,
                        frameon=True, framealpha=0.95)

    alarms_text = (
        f"nessun allarme {alarm_subject}" if n_alarms == 0
        else f"1 allarme {alarm_subject}" if n_alarms == 1
        else f"{n_alarms} allarmi {alarm_subject}"
    )
    ax_value.set_title(title, fontsize=13, fontweight="bold", pad=meta_offset + 19)
    add_session_subtitle(ax_value, f"{_subtitle(data)} · {alarms_text}",
                         title_pad=meta_offset + 19, meta_offset=meta_offset)
    data.save_figure(fig, filename)


def _hr_session(data: _Data) -> None:
    thr = APP_CONFIG.smartwatch_thresholds
    hr = data.hr_filtered
    # Il punto si disegna sul valore filtrato, mentre il confronto con le
    # soglie usa il battito arrotondato all'intero, come nel firmware.
    bpm = data.bpm
    with np.errstate(invalid="ignore"):
        hr_out = (
            np.isfinite(hr) & np.isfinite(bpm)
            & ((bpm > thr.bpm_max_in) | (bpm < thr.bpm_min_in))
        )
    _session(
        data,
        panel=dict(
            values=hr,
            band=(thr.bpm_min_in, thr.bpm_max_in),
            band_label=f"Entro le soglie di allarme ({thr.bpm_min_in}–{thr.bpm_max_in} bpm)",
            exit_lines=(thr.bpm_min_out, thr.bpm_max_out),
            exit_label=f"Soglie di rientro ({thr.bpm_min_out} e {thr.bpm_max_out} bpm)",
            out_mask=hr_out, unit="bpm", ylabel="Battito [bpm]",
            ylim=_padded_limits(hr, thr.bpm_min_out, thr.bpm_max_in, 8.0),
        ),
        alarm_times=data.hr_alarm_times, alarm_prefix="BPM",
        alarm_subject="sul battito",
        title="Andamento del battito cardiaco nella sessione",
        filename=_FILENAMES["hr_session"],
    )


def _spo2_session(data: _Data) -> None:
    thr = APP_CONFIG.smartwatch_thresholds
    spo2 = data.spo2_filtered
    with np.errstate(invalid="ignore"):
        spo2_out = np.isfinite(spo2) & np.isfinite(data.spo2) & (data.spo2 < thr.spo2_min_in)
    _session(
        data,
        panel=dict(
            values=spo2,
            band=(thr.spo2_min_in, 101.0),
            band_label=f"Entro la soglia di allarme (≥ {thr.spo2_min_in}%)",
            exit_lines=(thr.spo2_min_out,),
            exit_label=f"Soglia di rientro ({thr.spo2_min_out}%)",
            out_mask=spo2_out, unit="%", ylabel="SpO2 [%]",
            ylim=_padded_limits(spo2, thr.spo2_min_in, 100.0, 1.5, cap=100.8),
        ),
        alarm_times=data.spo2_alarm_times, alarm_prefix="SpO2",
        alarm_subject="sulla saturazione",
        title="Andamento della saturazione (SpO2) nella sessione",
        filename=_FILENAMES["spo2_session"],
    )


def save_watch_biometric_plots(logger, output_dir: Path, plt) -> list[Path]:
    saved_paths: list[Path] = []
    data = _prepare(logger, output_dir, plt, saved_paths)

    draw_each((_hr_comparison, _spo2_comparison, _hr_session, _spo2_session), data, plt, LOGGER)

    LOGGER.info("Grafici biometrici salvati in %s", output_dir)
    return saved_paths
