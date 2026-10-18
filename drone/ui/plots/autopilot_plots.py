from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, Patch
from matplotlib.ticker import FuncFormatter, NullFormatter
from matplotlib.transforms import blended_transform_factory

from drone.config import APP_CONFIG
from drone.data.flight_report_stats import elapsed_seconds, entries_with_pose
from drone.ui.plots.axes import (
    SAT_DASHES,
    add_session_subtitle,
    add_tolerance_tick,
    add_wp_change_markers,
    draw_each,
    figure_saver,
    ordered_legend,
    plain_number,
    style_axis,
)
from drone.ui.plots.palette import PALETTE

LOGGER = logging.getLogger(__name__)

_SAVE_DPI = 300

_FILENAMES = {
    "trajectory_xy": "autopilot_flown_trajectory.png",
    "distance_time": "autopilot_waypoint_distance.png",
    "rc_commands":   "autopilot_rc_commands.png",
    "xy_error":      "autopilot_xy_position_error.png",
    "z_error":       "autopilot_altitude_error.png",
    "yaw_error":     "autopilot_yaw_error.png",
}


class _VerticalSampleHandler(HandlerBase):
    def create_artists(self, legend, orig_handle, xdescent, ydescent,
                       width, height, fontsize, trans):
        line = Line2D(
            [width * 0.5 - xdescent] * 2, [-ydescent, height - ydescent],
            color=orig_handle.get_color(),
            linestyle=orig_handle.get_linestyle(),
            linewidth=orig_handle.get_linewidth(),
        )
        line.set_transform(trans)
        return [line]


@dataclass
class _Data:
    plt: object
    save_figure: Callable
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    lr: np.ndarray
    fb: np.ndarray
    ud: np.ndarray
    yaw_cmd: np.ndarray
    distance_3d: np.ndarray
    distance_xy: np.ndarray
    yaw_error: np.ndarray
    target_x: np.ndarray
    target_y: np.ndarray
    target_z: np.ndarray
    target_index: np.ndarray
    within_tol: Optional[np.ndarray]
    meta: str
    wp_change_times: np.ndarray
    wp_change_labels: list
    xy_tol: float
    z_tol: float
    yaw_tol: float
    sat_limits: dict
    colors: dict

    def wp_lines(self, axes_list) -> int:
        return add_wp_change_markers(
            axes_list, self.wp_change_times, self.wp_change_labels,
            float(self.t[-1]), self.colors["wp_vline"],
        )


def _prepare(logger, output_dir: Path, plt, saved_paths: list) -> _Data:
    entries = entries_with_pose(logger.autopilot_entries)
    t = np.array(elapsed_seconds(entries), dtype=np.float64)

    x       = np.array([e["x"]       for e in entries], dtype=np.float64)
    y       = np.array([e["y"]       for e in entries], dtype=np.float64)
    z       = np.array([e["z"]       for e in entries], dtype=np.float64)
    lr      = np.array([e["lr"]      for e in entries], dtype=np.float64)
    fb      = np.array([e["fb"]      for e in entries], dtype=np.float64)
    ud      = np.array([e["ud"]      for e in entries], dtype=np.float64)
    yaw_cmd = np.array([e["yaw_cmd"] for e in entries], dtype=np.float64)

    distance_3d = np.array(
        [np.nan if e["distance_3d"] is None else e["distance_3d"] for e in entries],
        dtype=np.float64,
    )
    distance_xy = np.array(
        [np.nan if e["distance_xy"] is None else e["distance_xy"] for e in entries],
        dtype=np.float64,
    )
    yaw_error = np.array(
        [np.nan if e["yaw_error_deg"] is None else e["yaw_error_deg"] for e in entries],
        dtype=np.float64,
    )
    target_x = np.array(
        [np.nan if e["target_x"]   is None else e["target_x"]   for e in entries],
        dtype=np.float64,
    )
    target_y = np.array(
        [np.nan if e["target_y"]   is None else e["target_y"]   for e in entries],
        dtype=np.float64,
    )
    target_z = np.array(
        [np.nan if e["target_z"]   is None else e["target_z"]   for e in entries],
        dtype=np.float64,
    )

    meta = f"{len(entries)} campioni · {float(t[-1]):.0f} s"

    target_indices = np.array(
        [-1 if e["target_index"] is None else int(e["target_index"]) for e in entries]
    )
    _wp_idx = np.where(
        np.diff(target_indices, prepend=target_indices[0] - 1) != 0
    )[0]
    _wp_idx = _wp_idx[target_indices[_wp_idx] >= 0]
    wp_change_times  = t[_wp_idx]
    wp_change_labels = [f"W{int(target_indices[i]) + 1}" for i in _wp_idx]

    autopilot_config = APP_CONFIG.apriltag_autopilot

    xy_tol = logger.autopilot_xy_tolerance_m
    z_tol = logger.autopilot_z_tolerance_m
    yaw_tol = logger.autopilot_yaw_tolerance_deg
    if xy_tol is None:
        xy_tol = autopilot_config.xy_tolerance_m
    if z_tol is None:
        z_tol = autopilot_config.z_tolerance_m
    if yaw_tol is None:
        yaw_tol = autopilot_config.yaw_tolerance_deg

    sat_limits = {
        "fb": int(autopilot_config.max_xy_speed),
        "lr": int(autopilot_config.max_xy_speed),
        "ud": int(autopilot_config.max_z_speed),
        "yaw_cmd": int(autopilot_config.max_yaw_speed),
    }

    colors = {
        "start":     PALETTE["partenza"],
        "end":       PALETTE["arrivo"],
        "target":    PALETTE["yaw_heading"],
        "distance":  PALETTE["distanza"],
        "z":         PALETTE["serie_blu"],
        "yaw":       PALETTE["serie_viola"],
        "fb":        PALETTE["serie_blu"],
        "lr":        PALETTE["serie_arancio"],
        "ud":        PALETTE["serie_verde"],
        "yaw_cmd":   PALETTE["serie_rosso"],
        "sat":       PALETTE["saturazione"],
        "grid":      PALETTE["griglia"],
        "wp_vline":  PALETTE["wp_vline"],
        "raw_trace": PALETTE["grezza"],
    }

    error_z_abs = np.abs(target_z - z)
    if (logger.autopilot_xy_tolerance_m is not None
            and logger.autopilot_z_tolerance_m is not None):
        within_tol = (
            (distance_xy <= logger.autopilot_xy_tolerance_m)
            & (error_z_abs <= logger.autopilot_z_tolerance_m)
        )
    else:
        within_tol = None

    return _Data(
        plt=plt,
        save_figure=figure_saver(output_dir, _SAVE_DPI, saved_paths, plt),
        t=t, x=x, y=y, z=z, lr=lr, fb=fb, ud=ud, yaw_cmd=yaw_cmd,
        distance_3d=distance_3d, distance_xy=distance_xy, yaw_error=yaw_error,
        target_x=target_x, target_y=target_y, target_z=target_z,
        target_index=target_indices, within_tol=within_tol,
        meta=meta, wp_change_times=wp_change_times,
        wp_change_labels=wp_change_labels,
        xy_tol=xy_tol, z_tol=z_tol, yaw_tol=yaw_tol,
        sat_limits=sat_limits, colors=colors,
    )


def _smooth_path(values, sigma):
    n = values.size
    if sigma <= 0 or n < 3:
        return values
    radius = max(1, int(round(3.0 * sigma)))
    radius = min(radius, n - 1)
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-(offsets ** 2) / (2.0 * sigma ** 2))
    kernel /= kernel.sum()
    padded = np.pad(values, radius, mode="reflect")
    return np.convolve(padded, kernel, mode="valid")


def _trajectory(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    x, y, xy_tol = data.x, data.y, data.xy_tol
    target_x, target_y, target_index = data.target_x, data.target_y, data.target_index
    plot_filenames = _FILENAMES

    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)

    valid_xy = np.isfinite(x) & np.isfinite(y)
    if np.count_nonzero(valid_xy) >= 2:
        xv, yv = x[valid_xy], y[valid_xy]
        sigma = float(np.clip(xv.size * 0.02, 3.0, 25.0))
        xs = _smooth_path(xv, sigma)
        ys = _smooth_path(yv, sigma)

        ax.plot(xs, ys, linewidth=2.4, alpha=0.95, zorder=3,
                color=PALETTE["filtrata"], label="Traiettoria")

        ax.scatter([xs[0]],  [ys[0]],  s=90, marker="^",
                   color=colors["start"], edgecolors="black",
                   linewidths=0.9, zorder=6, label="Partenza")
        ax.scatter([xs[-1]], [ys[-1]], s=90, marker="s",
                   color=colors["end"],   edgecolors="black",
                   linewidths=0.9, zorder=6, label="Arrivo")

    valid_tgt = np.isfinite(target_x) & np.isfinite(target_y)
    if np.any(valid_tgt):
        steps_at_pos: dict[tuple[float, float], list[int]] = {}
        pos_order: list[tuple[float, float]] = []
        last_idx = None
        for ti, tx_, ty_ in zip(target_index, target_x, target_y):
            if ti < 0 or not np.isfinite(tx_) or not np.isfinite(ty_):
                continue
            ti = int(ti)
            if ti == last_idx:
                continue
            last_idx = ti
            pos = (float(tx_), float(ty_))
            key = (round(pos[0], 4), round(pos[1], 4))
            if key not in steps_at_pos:
                steps_at_pos[key] = []
                pos_order.append(pos)
            steps_at_pos[key].append(ti + 1)

        for i, (wx, wy) in enumerate(pos_order):
            key = (round(wx, 4), round(wy, 4))
            label_text = "W" + "/".join(str(s) for s in steps_at_pos[key])
            ax.add_patch(Circle(
                (wx, wy), xy_tol, facecolor=colors["target"], alpha=0.12,
                zorder=3,
            ))
            ax.add_patch(Circle(
                (wx, wy), xy_tol, fill=False, linestyle="--",
                linewidth=1.6, edgecolor=colors["target"], alpha=0.9, zorder=4,
                label="Margine di tolleranza" if i == 0 else None,
            ))
            ax.scatter([wx], [wy], s=110, marker="X",
                       color=colors["target"], edgecolors="black",
                       linewidths=0.9, zorder=5,
                       label="Waypoint" if i == 0 else None)
            ax.annotate(label_text, (wx, wy),
                        textcoords="offset points", xytext=(9, 6),
                        fontsize=9.5, fontweight="bold", color=PALETTE["waypoint_testo"],
                        ha="left", va="bottom", zorder=6)

    style_axis(ax, "X [m]", "Y [m]", "Traiettoria percorsa")
    for side in ("top", "right"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_linewidth(0.8)
    ax.set_aspect("equal", adjustable="box")
    ax.margins(0.14)
    ordered_legend(
        ax,
        {
            "Partenza": 0,
            "Arrivo": 1,
            "Waypoint": 2,
            "Traiettoria": 3,
            "Margine di tolleranza": 4,
        },
        loc="upper center", bbox_to_anchor=(0.5, -0.08),
        ncol=3, fontsize=9, frameon=True, framealpha=0.95,
    )
    add_session_subtitle(ax, f"Sessione autopilota  ·  {meta}")
    save_figure(fig, plot_filenames["trajectory_xy"])


def _distance(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    t, distance_3d, within_tol = data.t, data.distance_3d, data.within_tol
    add_wp_vlines = data.wp_lines
    plot_filenames = _FILENAMES

    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)

    valid_d = np.isfinite(t) & np.isfinite(distance_3d)
    if np.any(valid_d):
        td, dd = t[valid_d], distance_3d[valid_d]
        ax.plot(td, dd, linewidth=1.9, color=colors["distance"], zorder=3)
        if within_tol is not None:
            within_tol_valid = within_tol[valid_d]
            ax.fill_between(td, dd, 0, where=within_tol_valid,
                            alpha=0.18, color=PALETTE["tolleranza"],
                            label="Posizione entro tolleranza", zorder=2)
            ax.fill_between(td, dd, 0, where=~within_tol_valid,
                            alpha=0.18, color=PALETTE["fuori_tol"],
                            label="Posizione fuori tolleranza", zorder=2)
        mean_d = float(np.nanmean(dd))
        ax.axhline(mean_d, linestyle=":", linewidth=1.2,
                   color=PALETTE["media"], alpha=0.9, zorder=4)
        ax.annotate(
            f"media  {mean_d:.2f} m", xy=(1.0, mean_d),
            xycoords=blended_transform_factory(ax.transAxes, ax.transData),
            xytext=(5, 0), textcoords="offset points",
            ha="left", va="center", fontsize=8, color=PALETTE["media"],
            annotation_clip=False,
        )
    else:
        ax.plot(t, np.zeros_like(t), linewidth=1.5,
                color=colors["distance"], label="distanza non disponibile")

    style_axis(ax, "Tempo [s]", "Distanza 3D [m]",
                     "Distanza dal waypoint di destinazione")
    ax.set_ylim(bottom=0.0)
    meta_offset = add_wp_vlines([ax])
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2,
                  fontsize=9, frameon=True, framealpha=0.95)
    add_session_subtitle(ax, f"Sessione autopilota  ·  {meta}",
                         title_pad=meta_offset + 19, meta_offset=meta_offset)
    save_figure(fig, plot_filenames["distance_time"])


def _rc_commands(data: _Data) -> None:
    plt, save_figure, colors, meta = data.plt, data.save_figure, data.colors, data.meta
    t, fb, lr, ud, yaw_cmd = data.t, data.fb, data.lr, data.ud, data.yaw_cmd
    sat_limits, wp_change_times = data.sat_limits, data.wp_change_times
    add_wp_vlines = data.wp_lines
    plot_filenames = _FILENAMES

    rc_series = [
        (fb,      colors["fb"],      "Avanti / Indietro  (fb)", "fb"),
        (lr,      colors["lr"],      "Sinistra / Destra  (lr)", "lr"),
        (ud,      colors["ud"],      "Su / Giù  (ud)",          "ud"),
        (yaw_cmd, colors["yaw_cmd"], "Rotazione  (yaw)",        "yaw_cmd"),
    ]

    def panel_limit(vals, satkey):
        finite = vals[np.isfinite(vals)]
        span = float(np.max(np.abs(finite))) if finite.size else 0.0
        span = max(span, float(sat_limits[satkey]))
        return max(5.0, span * 1.15)

    fig, axes = plt.subplots(4, 1, figsize=(11, 8),
                             sharex=True, constrained_layout=True)

    for ax_i, (vals, color, ylabel, satkey) in zip(axes, rc_series):
        vals = np.asarray(vals, dtype=np.float64)
        rc_limit = panel_limit(vals, satkey)
        valid = np.isfinite(t) & np.isfinite(vals)
        if np.any(valid):
            ax_i.plot(t[valid], vals[valid], linewidth=1.5,
                      color=color, zorder=3)
            ax_i.fill_between(t[valid], vals[valid], 0,
                              alpha=0.18, color=color, zorder=2)
        lim = sat_limits[satkey]
        limit_label_transform = blended_transform_factory(ax_i.transAxes,
                                                          ax_i.transData)
        for limit_y, limit_text in ((lim, f"{lim}"), (-lim, f"−{lim}")):
            ax_i.annotate(
                limit_text, xy=(1.0, limit_y), xycoords=limit_label_transform,
                xytext=(5, 0), textcoords="offset points",
                ha="left", va="center", fontsize=8, color=colors["sat"],
                annotation_clip=False,
            )
        for limit_y in (lim, -lim):
            ax_i.axhline(limit_y, linewidth=1.2, color=colors["sat"],
                         linestyle=SAT_DASHES, alpha=0.9, zorder=4)
        if np.any(valid):
            saturated = valid & (np.abs(vals) >= lim - 0.5)
            if np.any(saturated):
                ax_i.fill_between(t, -rc_limit, rc_limit, where=saturated,
                                  color=colors["sat"], alpha=0.14, zorder=1,
                                  step="mid")
        ax_i.axhline(0, linewidth=0.8, color=PALETTE["asse_zero"], alpha=0.35, zorder=4)
        ax_i.set_ylabel(ylabel, fontsize=9.5)
        ax_i.set_ylim(-rc_limit, rc_limit)
        ax_i.grid(True, linestyle="-",  linewidth=0.5, alpha=0.40,
                  color=colors["grid"])
        ax_i.minorticks_on()
        ax_i.grid(True, which="minor", linestyle=":", linewidth=0.3,
                  alpha=0.20, color=colors["grid"])
        ax_i.spines["top"].set_visible(False)
        ax_i.spines["right"].set_visible(False)
        ax_i.tick_params(labelsize=9)

    axes[-1].set_xlabel("Tempo [s]", fontsize=11)

    meta_offset = add_wp_vlines(list(axes))

    axes[0].set_title("Comandi RC", fontsize=13, fontweight="bold",
                      pad=meta_offset + 19)
    add_session_subtitle(axes[0], f"Sessione autopilota  ·  {meta}",
                         title_pad=meta_offset + 19, meta_offset=meta_offset)


    rc_legend = []
    rc_legend.append(Line2D([0], [0], color=colors["sat"], linestyle=SAT_DASHES,
                            linewidth=1.2, alpha=0.9,
                            label="Limite di saturazione"))
    rc_legend.append(Patch(facecolor=colors["sat"], alpha=0.14,
                           label="Canale saturo"))
    wp_handle = None
    if len(wp_change_times):
        wp_handle = Line2D([0], [0], color=colors["wp_vline"], linestyle="--",
                           linewidth=1.1, label="Cambio waypoint")
        rc_legend.append(wp_handle)
    if rc_legend:
        axes[-1].legend(handles=rc_legend, loc="upper center",
                        bbox_to_anchor=(0.5, -0.32), ncol=len(rc_legend),
                        fontsize=9, frameon=True, framealpha=0.95,
                        handleheight=2.0, handlelength=2.0,
                        handler_map={} if wp_handle is None
                        else {wp_handle: _VerticalSampleHandler()})

    save_figure(fig, plot_filenames["rc_commands"])


def _error_over_time(data: _Data, *, series, tol, tol_text, ylabel, title,
                     linthresh, ncol, filename) -> None:
    t, meta = data.t, data.meta

    fig, ax = data.plt.subplots(figsize=(11, 5), constrained_layout=True)
    for values, color, label in series:
        valid = np.isfinite(t) & np.isfinite(values)
        if np.any(valid):
            ax.plot(t[valid], values[valid], linewidth=1.6, color=color,
                    label=label, zorder=3)
    ax.axhspan(-tol, tol, color=PALETTE["tolleranza"], alpha=0.12, zorder=1,
               label=f"Oltre {tol_text}: fuori tolleranza")
    ax.axhline(0, linewidth=0.8, color=PALETTE["asse_zero"], alpha=0.35, zorder=4)
    style_axis(ax, "Tempo [s]", ylabel, title)
    if linthresh is not None:
        ax.set_yscale("symlog", linthresh=linthresh)
        ax.yaxis.set_major_formatter(FuncFormatter(plain_number))
        ax.yaxis.set_minor_formatter(NullFormatter())
        add_tolerance_tick(ax, tol)
    meta_offset = data.wp_lines([ax])
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=ncol,
              fontsize=9, frameon=True, framealpha=0.95)
    add_session_subtitle(ax, f"Sessione autopilota  ·  {meta}",
                         title_pad=meta_offset + 19, meta_offset=meta_offset)
    data.save_figure(fig, filename)


def _error_xy(data: _Data) -> None:
    tol = data.xy_tol
    _error_over_time(
        data,
        series=(
            (data.target_x - data.x, data.colors["fb"],
             "Errore su X (waypoint − posa stimata)"),
            (data.target_y - data.y, data.colors["lr"],
             "Errore su Y (waypoint − posa stimata)"),
        ),
        tol=tol, tol_text=f"±{tol:.2f} m",
        ylabel="Errore [m]", title="Errore di posizione X e Y",
        linthresh=max(2.0 * tol, 0.2), ncol=3,
        filename=_FILENAMES["xy_error"],
    )


def _error_z(data: _Data) -> None:
    tol = data.z_tol
    _error_over_time(
        data,
        series=(
            (data.target_z - data.z, data.colors["z"],
             "Errore di quota (waypoint − posa stimata)"),
        ),
        tol=tol, tol_text=f"±{tol:.2f} m",
        ylabel="Errore [m]", title="Errore di quota (Z)",
        linthresh=None, ncol=2,
        filename=_FILENAMES["z_error"],
    )


def _error_yaw(data: _Data) -> None:
    tol = data.yaw_tol
    _error_over_time(
        data,
        series=(
            (data.yaw_error, data.colors["yaw"],
             "Errore di orientamento (waypoint − posa stimata)"),
        ),
        tol=tol, tol_text=f"±{tol:.1f}°",
        ylabel="Errore [°]", title="Errore di orientamento (yaw)",
        linthresh=max(2.0 * tol, 10.0), ncol=2,
        filename=_FILENAMES["yaw_error"],
    )


def save_autopilot_plots(logger, output_dir: Path, plt) -> list[Path]:
    saved_paths: list[Path] = []
    data = _prepare(logger, output_dir, plt, saved_paths)

    draw_each(
        (_trajectory, _distance, _rc_commands, _error_xy, _error_z, _error_yaw),
        data, plt, LOGGER,
    )

    LOGGER.info("Grafici autopilota salvati in %s", output_dir)
    return saved_paths
