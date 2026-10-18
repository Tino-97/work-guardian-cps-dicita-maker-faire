from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from matplotlib.transforms import blended_transform_factory

from drone.data.flight_report_stats import elapsed_seconds
from drone.ui.plots.axes import (
    add_session_subtitle,
    draw_each,
    figure_saver,
    ordered_legend,
    style_axis,
)
from drone.ui.plots.palette import PALETTE

LOGGER = logging.getLogger(__name__)

_SAVE_DPI = 300

_FILENAMES = {
    "trajectory_xy":   "kalman_raw_and_filtered_trajectory.png",
    "correction_norm": "kalman_filter_correction.png",
    "yaw_time":        "kalman_raw_and_filtered_yaw.png",
    "xy_time":         "kalman_raw_and_filtered_position.png",
}


@dataclass
class _Data:
    plt: object
    save_figure: Callable
    t: np.ndarray
    raw_x: np.ndarray
    raw_y: np.ndarray
    filtered_x: np.ndarray
    filtered_y: np.ndarray
    raw_yaw: np.ndarray
    filtered_yaw: np.ndarray
    error_norm: np.ndarray
    outlier_rejected: np.ndarray
    meta: str
    colors: dict


def _prepare(logger, output_dir: Path, plt, saved_paths: list) -> _Data:
    entries = logger.comparison_entries
    t = np.array(elapsed_seconds(entries), dtype=np.float64)

    raw_x = np.array([entry["raw_x"] for entry in entries], dtype=np.float64)
    raw_y = np.array([entry["raw_y"] for entry in entries], dtype=np.float64)

    filtered_x = np.array([entry["filtered_x"] for entry in entries], dtype=np.float64)
    filtered_y = np.array([entry["filtered_y"] for entry in entries], dtype=np.float64)

    raw_yaw = np.array([entry["raw_yaw_deg"] for entry in entries], dtype=np.float64)
    filtered_yaw = np.array([entry["filtered_yaw_deg"] for entry in entries], dtype=np.float64)

    error_norm = np.array([entry["error_norm"] for entry in entries], dtype=np.float64)
    outlier_rejected = np.array(
        [bool(entry.get("outlier_rejected", False)) for entry in entries]
    )

    meta = f"{len(entries)} campioni · {float(t[-1]):.0f} s"

    colors = {
        "raw":      PALETTE["grezza"],
        "filtered": PALETTE["filtrata"],
        "start":    PALETTE["partenza"],
        "end":      PALETTE["arrivo"],
        "grid":     PALETTE["griglia"],
        "norm":     PALETTE["filtrata"],
        "outlier":  PALETTE["arrivo"],
        "raw_yaw":  PALETTE["grezza"],
        "filt_yaw": PALETTE["serie_viola"],
    }

    return _Data(
        plt=plt,
        save_figure=figure_saver(output_dir, _SAVE_DPI, saved_paths, plt),
        t=t, raw_x=raw_x, raw_y=raw_y,
        filtered_x=filtered_x, filtered_y=filtered_y,
        raw_yaw=raw_yaw, filtered_yaw=filtered_yaw,
        error_norm=error_norm, outlier_rejected=outlier_rejected,
        meta=meta, colors=colors,
    )


def _wrap_deg(values):
    out = np.asarray(values, dtype=np.float64)
    return ((out + 180.0) % 360.0) - 180.0


def _break_angle_wrap(values, threshold_deg: float = 180.0):
    out = np.asarray(values, dtype=np.float64).copy()
    if out.size < 2:
        return out
    jumps = np.abs(np.diff(out)) > threshold_deg
    out[:-1][jumps] = np.nan
    return out


def _trajectory(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    raw_x, raw_y = data.raw_x, data.raw_y
    filtered_x, filtered_y = data.filtered_x, data.filtered_y
    plot_filenames = _FILENAMES

    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)

    valid_raw_xy_mask = np.isfinite(raw_x) & np.isfinite(raw_y)
    valid_filtered_xy_mask = np.isfinite(filtered_x) & np.isfinite(filtered_y)

    if np.any(valid_raw_xy_mask):
        ax.plot(
            raw_x[valid_raw_xy_mask],
            raw_y[valid_raw_xy_mask],
            linewidth=2.6,
            alpha=0.55,
            color=colors["raw"],
            label="Traiettoria grezza",
            zorder=2,
        )

    if np.any(valid_filtered_xy_mask):
        filtered_x_valid = filtered_x[valid_filtered_xy_mask]
        filtered_y_valid = filtered_y[valid_filtered_xy_mask]

        ax.plot(
            filtered_x_valid,
            filtered_y_valid,
            linewidth=1.4,
            color=colors["filtered"],
            label="Traiettoria filtrata",
            zorder=3,
        )

        ax.scatter(
            [filtered_x_valid[0]],
            [filtered_y_valid[0]],
            s=70,
            marker="o",
            color=colors["start"],
            edgecolors="black",
            linewidths=0.6,
            zorder=4,
            label="Partenza",
        )

        ax.scatter(
            [filtered_x_valid[-1]],
            [filtered_y_valid[-1]],
            s=80,
            marker="s",
            color=colors["end"],
            edgecolors="black",
            linewidths=0.6,
            zorder=4,
            label="Arrivo",
        )

    style_axis(
        ax,
        xlabel="X [m]",
        ylabel="Y [m]",
        title="Traiettoria: grezza vs filtrata",
    )
    for side in ("top", "right"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_linewidth(0.8)

    ax.set_aspect("equal", adjustable="box")
    ax.margins(0.10)
    ordered_legend(
        ax,
        {
            "Partenza": 0,
            "Arrivo": 1,
            "Traiettoria filtrata": 2,
            "Traiettoria grezza": 3,
        },
        loc="upper center", bbox_to_anchor=(0.5, -0.08),
        ncol=4, fontsize=9, frameon=True, framealpha=0.95,
    )
    add_session_subtitle(ax, f"Filtro Kalman  ·  {meta}")
    save_figure(fig, plot_filenames["trajectory_xy"])


def _correction(data: _Data) -> None:
    save_figure, colors, meta = data.save_figure, data.colors, data.meta
    t, error_norm, outlier_rejected = data.t, data.error_norm, data.outlier_rejected
    plt = data.plt
    plot_filenames = _FILENAMES

    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    valid_n = np.isfinite(t) & np.isfinite(error_norm)
    if np.any(valid_n):
        ax.plot(t[valid_n], error_norm[valid_n], linewidth=1.6,
                color=colors["norm"], zorder=3,
                label="Correzione applicata dal filtro")
        ax.fill_between(t[valid_n], error_norm[valid_n], 0,
                        alpha=0.15, color=colors["norm"], zorder=2)
        mean_n = float(np.nanmean(error_norm[valid_n]))
        ax.axhline(mean_n, linestyle=":", linewidth=1.2, color=PALETTE["media"],
                   alpha=0.9, zorder=4)
        ax.annotate(
            f"media  {mean_n:.3f} m", xy=(1.0, mean_n),
            xycoords=blended_transform_factory(ax.transAxes, ax.transData),
            xytext=(5, 0), textcoords="offset points",
            ha="left", va="center", fontsize=8, color=PALETTE["media"],
            annotation_clip=False,
        )

    out_mask = valid_n & outlier_rejected
    n_scartate = int(np.count_nonzero(out_mask))
    if np.any(out_mask):
        ax.scatter(t[out_mask], error_norm[out_mask], s=55, marker="x",
                   color=colors["outlier"], linewidths=1.6, zorder=5,
                   label=f"Misure scartate dal filtro ({n_scartate})")

    style_axis(ax, "Tempo [s]", "Correzione [m]",
                     "Correzione del filtro Kalman")
    ax.set_ylim(bottom=0.0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2,
              fontsize=9, frameon=True, framealpha=0.95)
    add_session_subtitle(ax, f"Filtro Kalman  ·  {meta}")
    save_figure(fig, plot_filenames["correction_norm"])


def _yaw(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    t, raw_yaw, filtered_yaw = data.t, data.raw_yaw, data.filtered_yaw
    plot_filenames = _FILENAMES

    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    raw_yaw_plot = _break_angle_wrap(_wrap_deg(raw_yaw))
    filt_yaw_plot = _break_angle_wrap(_wrap_deg(filtered_yaw))
    if np.any(np.isfinite(raw_yaw_plot)):
        ax.plot(t, raw_yaw_plot, linewidth=1.1, alpha=0.8,
                color=colors["raw_yaw"], label="Orientamento grezzo", zorder=2)
    if np.any(np.isfinite(filt_yaw_plot)):
        ax.plot(t, filt_yaw_plot, linewidth=1.9,
                color=colors["filt_yaw"], label="Orientamento filtrato", zorder=3)
    ax.axhline(0, linewidth=0.8, color=PALETTE["asse_zero"], alpha=0.30, zorder=1)
    style_axis(ax, "Tempo [s]", "Orientamento [°]",
                     "Orientamento (yaw): grezzo vs filtrato")
    ordered_legend(
        ax,
        {"Orientamento filtrato": 0, "Orientamento grezzo": 1},
        loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=9,
        frameon=True, framealpha=0.95,
    )
    add_session_subtitle(ax, f"Filtro Kalman  ·  {meta}")
    save_figure(fig, plot_filenames["yaw_time"])


def _position(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    t, raw_x, raw_y = data.t, data.raw_x, data.raw_y
    filtered_x, filtered_y = data.filtered_x, data.filtered_y
    plot_filenames = _FILENAMES

    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                             constrained_layout=True)
    axis_series = [
        (axes[0], raw_x, filtered_x, "X [m]"),
        (axes[1], raw_y, filtered_y, "Y [m]"),
    ]
    for ax_i, raw_s, filt_s, ylabel in axis_series:
        vr = np.isfinite(t) & np.isfinite(raw_s)
        vf = np.isfinite(t) & np.isfinite(filt_s)
        if np.any(vr):
            ax_i.plot(t[vr], raw_s[vr], linewidth=2.4, alpha=0.5,
                      color=colors["raw"], label="Posizione grezza", zorder=2)
        if np.any(vf):
            ax_i.plot(t[vf], filt_s[vf], linewidth=1.4,
                      color=colors["filtered"], label="Posizione filtrata", zorder=3)
        ax_i.set_ylabel(ylabel, fontsize=10.5)
        ax_i.grid(True, linestyle="-", linewidth=0.5, alpha=0.40, color=colors["grid"])
        ax_i.minorticks_on()
        ax_i.grid(True, which="minor", linestyle=":", linewidth=0.3,
                  alpha=0.20, color=colors["grid"])
        ax_i.spines["top"].set_visible(False)
        ax_i.spines["right"].set_visible(False)
        ax_i.tick_params(labelsize=9)
    axes[-1].set_xlabel("Tempo [s]", fontsize=11)
    ordered_legend(
        axes[-1],
        {"Posizione filtrata": 0, "Posizione grezza": 1},
        loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2, fontsize=9,
        frameon=True, framealpha=0.95,
    )

    axes[0].set_title("Posizione X e Y: grezza vs filtrata",
                      fontsize=13, fontweight="bold", pad=26)
    add_session_subtitle(axes[0], f"Filtro Kalman  ·  {meta}")
    save_figure(fig, plot_filenames["xy_time"])


def save_kalman_plots(logger, output_dir: Path, plt) -> list[Path]:
    saved_paths: list[Path] = []
    data = _prepare(logger, output_dir, plt, saved_paths)

    draw_each((_trajectory, _correction, _yaw, _position), data, plt, LOGGER)

    LOGGER.info("Grafici Kalman salvati in %s", output_dir)
    return saved_paths
