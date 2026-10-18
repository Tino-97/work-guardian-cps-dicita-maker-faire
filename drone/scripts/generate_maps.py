import argparse
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Circle, Patch, Polygon
from matplotlib.lines import Line2D
from matplotlib.colors import to_rgba
from matplotlib.legend_handler import HandlerBase
from matplotlib.text import Text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = PROJECT_ROOT / "drone" / "maps"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from drone.config import APP_CONFIG  # noqa: E402
from drone.loaders.waypoint_path_loader import load_waypoint_paths  # noqa: E402
from drone.config.logging_setup import ensure_utf8_console  # noqa: E402
from drone.ui.plots.palette import PALETTE  # noqa: E402


COLOR_MARKER_FLOOR = PALETTE["tag_pavimento"]
COLOR_MARKER_HIGH = PALETTE["tag_quota"]
COLOR_PATH = PALETTE["percorso"]
COLOR_WAYPOINT = PALETTE["waypoint"]
COLOR_SUPERVISION = PALETTE["supervisione"]
COLOR_HOME = PALETTE["home"]
COLOR_YAW = PALETTE["yaw_heading"]
COLOR_SITE = PALETTE["cantiere"]
COLOR_RESTRICTED = PALETTE["area_interdetta"]

SITE_FILL_ALPHA = 0.06

MARKER_FILL_ALPHA = 0.20

RESTRICTED_FILL_ALPHA = 0.14

MARKER_HIGH_Z_THRESHOLD = 0.75

MARKER_TAG_SIZE = 240
MARKER_WAYPOINT_SIZE = 760

SYMBOL_MIN_SCALE = 0.55

TAG_LABEL_SIDE = {11: 1.0}


class _Symbol:
    __slots__ = ("x", "y", "base_size", "patch", "inner", "base_fontsize")

    def __init__(self, x, y, base_size, patch, inner, base_fontsize):
        self.x = x
        self.y = y
        self.base_size = base_size
        self.patch = patch
        self.inner = inner
        self.base_fontsize = base_fontsize

    def apply_scale(self, scale):
        self.patch.set_sizes([self.base_size * scale * scale])
        self.inner.set_fontsize(self.base_fontsize * scale)


def _heading_vector(yaw_deg, length):
    yaw_rad = math.radians(yaw_deg)
    return length * math.cos(yaw_rad), length * math.sin(yaw_rad)


def _marker_color(z):
    return COLOR_MARKER_HIGH if z >= MARKER_HIGH_Z_THRESHOLD else COLOR_MARKER_FLOOR


def _add_coord_label(ax, x, y, z, color):
    return ax.text(
        x, y,
        f"({x:.2f} m, {y:.2f} m, {z:.2f} m)",
        fontsize=7.5,
        color=color,
        ha="left",
        va="center",
        zorder=7,
        bbox=dict(boxstyle="round,pad=0.12", facecolor="white",
                  edgecolor="none", alpha=0.7),
    )


def _draw_site_area(ax, vertices):
    if not vertices:
        return ()
    ax.add_patch(
        Polygon(
            list(vertices),
            closed=True,
            facecolor=to_rgba(COLOR_SITE, SITE_FILL_ALPHA),
            edgecolor=COLOR_SITE,
            linewidth=2.0,
            zorder=1,
        )
    )
    return tuple(
        (tuple(vertices[i]), tuple(vertices[(i + 1) % len(vertices)]))
        for i in range(len(vertices))
    )


def _draw_restricted_areas(ax, areas):
    edges = []
    for vertices in areas:
        ax.add_patch(
            Polygon(
                list(vertices),
                closed=True,
                facecolor=to_rgba(COLOR_RESTRICTED, RESTRICTED_FILL_ALPHA),
                edgecolor=COLOR_RESTRICTED,
                hatch="///",
                linewidth=1.8,
                zorder=3,
            )
        )
        edges += [
            (tuple(vertices[i]), tuple(vertices[(i + 1) % len(vertices)]))
            for i in range(len(vertices))
        ]
    return tuple(edges)


def _draw_markers(ax, world_tags, show_coords):
    coord_texts, coord_owners, symbols = [], [], []
    coord_sides = {}

    if not world_tags:
        return coord_texts, coord_owners, symbols, coord_sides

    for tag_id, pose in sorted(world_tags.items()):
        x, y, z = pose.position_m
        color = _marker_color(z)

        patch = ax.scatter(
            x, y,
            marker="s",
            s=MARKER_TAG_SIZE,
            facecolors=to_rgba(color, MARKER_FILL_ALPHA),
            edgecolors=color,
            linewidths=2.2,
            zorder=4,
        )

        inner = ax.annotate(
            str(tag_id),
            xy=(x, y),
            ha="center",
            va="center",
            fontsize=8,
            fontweight="bold",
            color=color,
            zorder=5,
        )
        symbols.append(_Symbol(x, y, MARKER_TAG_SIZE, patch, inner, 8))

        if show_coords:
            coord_texts.append(_add_coord_label(ax, x, y, z, color))
            coord_owners.append((x, y))
            if tag_id in TAG_LABEL_SIDE:
                coord_sides[(round(x, 6), round(y, 6))] = TAG_LABEL_SIDE[tag_id]

    return coord_texts, coord_owners, symbols, coord_sides


def _draw_route_arrows(ax, waypoints):
    segments = []
    for current, nxt in zip(waypoints, waypoints[1:]):
        ax.add_patch(
            FancyArrowPatch(
                (current.x, current.y),
                (nxt.x, nxt.y),
                arrowstyle="-|>",
                mutation_scale=16,
                color=COLOR_PATH,
                linewidth=1.8,
                shrinkA=14,
                shrinkB=14,
                zorder=2,
            )
        )
        segments.append(((current.x, current.y), (nxt.x, nxt.y)))
    return segments


def _group_coincident_waypoints(waypoints):
    grouped = {}
    for index, wp in enumerate(waypoints, start=1):
        key = (round(wp.x, 3), round(wp.y, 3))
        grouped.setdefault(key, []).append((index, wp))
    return grouped


def _waypoint_color(indices, supervision_1based, home_1based):
    if home_1based is not None and home_1based in indices:
        return COLOR_HOME
    if any(idx in supervision_1based for idx in indices):
        return COLOR_SUPERVISION
    return COLOR_WAYPOINT


def _waypoint_label(indices, home_1based):
    return "/".join(
        "H" if (home_1based is not None and idx == home_1based) else str(idx)
        for idx in indices
    )


def _label_fontsize(label):
    n_label = len(label)
    if n_label <= 2:
        return 11.5
    if n_label <= 3:
        return 11.0
    if n_label <= 4:
        return 10.0
    if n_label <= 5:
        return 8.5
    return 6.5


def _draw_yaw_arrows(ax, entries, wp, arrow_length, yaw_offset):
    segments = []
    distinct_yaw = {}
    for _, w in entries:
        distinct_yaw.setdefault(round(w.yaw_deg, 3), w.yaw_deg)
    for yaw_deg in distinct_yaw.values():
        dx, dy = _heading_vector(yaw_deg + yaw_offset, arrow_length * 1.7)
        ax.add_patch(
            FancyArrowPatch(
                (wp.x, wp.y),
                (wp.x + dx, wp.y + dy),
                arrowstyle="-|>",
                mutation_scale=18,
                color=COLOR_YAW,
                linewidth=2.8,
                zorder=5,
            )
        )
        segments.append(((wp.x, wp.y), (wp.x + dx, wp.y + dy)))
    return segments


def _draw_path(ax, waypoints, arrow_length, show_coords, show_yaw, supervision_1based,
               yaw_offset, home_1based=None):
    supervision_1based = set(supervision_1based)

    coord_texts, coord_owners, segments, symbols = [], [], [], []

    if not waypoints:
        return coord_texts, coord_owners, segments, symbols

    segments += _draw_route_arrows(ax, waypoints)

    for entries in _group_coincident_waypoints(waypoints).values():
        indices = [idx for idx, _ in entries]
        wp = entries[0][1]
        color = _waypoint_color(indices, supervision_1based, home_1based)
        label = _waypoint_label(indices, home_1based)

        if show_yaw:
            segments += _draw_yaw_arrows(ax, entries, wp, arrow_length, yaw_offset)

        label_fontsize = _label_fontsize(label)
        patch = ax.scatter(
            wp.x, wp.y,
            marker="o",
            s=MARKER_WAYPOINT_SIZE,
            facecolors=color,
            edgecolors="black",
            linewidths=1.2,
            zorder=6,
        )

        inner = ax.annotate(
            label,
            xy=(wp.x, wp.y),
            ha="center",
            va="center",
            fontsize=label_fontsize,
            color="white",
            fontweight="bold",
            zorder=7,
        )
        symbols.append(
            _Symbol(wp.x, wp.y, MARKER_WAYPOINT_SIZE, patch, inner, label_fontsize)
        )

        if show_coords:
            coord_texts.append(_add_coord_label(ax, wp.x, wp.y, wp.z, "black"))
            coord_owners.append((wp.x, wp.y))

    return coord_texts, coord_owners, segments, symbols



class _HandlerHomeMarker(HandlerBase):
    def create_artists(self, legend, orig_handle, xdescent, ydescent,
                        width, height, fontsize, trans):
        cx = width / 2.0 - xdescent
        cy = height / 2.0 - ydescent
        radius = height * 0.72
        circle = Circle(
            (cx, cy), radius,
            facecolor=COLOR_HOME, edgecolor="black", linewidth=1.0,
        )
        circle.set_transform(trans)
        label = Text(
            cx, cy, "H",
            ha="center", va="center",
            color="white", fontweight="bold", fontsize=fontsize * 0.72,
        )
        label.set_transform(trans)
        return [circle, label]


def _build_legend(ax, *, markers, waypoints, show_yaw, home=False, supervision=False,
                  floor_tags=False, high_tags=False, site_area=False,
                  restricted_areas=False):
    handles = []
    home_handle = None

    if waypoints:
        handles.append(
            Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_WAYPOINT,
                   markeredgecolor="black", markersize=10, label="Waypoint di passaggio")
        )
        if supervision:
            handles.append(
                Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_SUPERVISION,
                       markeredgecolor="black", markersize=10, label="Waypoint di supervisione")
            )
        if home:
            home_handle = Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_HOME,
                                 markeredgecolor="black", markersize=10,
                                 label="Waypoint di rientro e atterraggio")
            handles.append(home_handle)

    if site_area:
        handles.append(
            Patch(
                facecolor=to_rgba(COLOR_SITE, SITE_FILL_ALPHA),
                edgecolor=COLOR_SITE, linewidth=1.6,
                label="Perimetro del cantiere",
            )
        )
    if restricted_areas:
        handles.append(
            Patch(
                facecolor=to_rgba(COLOR_RESTRICTED, RESTRICTED_FILL_ALPHA),
                edgecolor=COLOR_RESTRICTED, linewidth=1.6, hatch="///",
                label="Area interdetta",
            )
        )
    if markers:
        if floor_tags:
            handles.append(
                Line2D([0], [0], marker="s", color="none",
                       markerfacecolor=to_rgba(COLOR_MARKER_FLOOR, MARKER_FILL_ALPHA),
                       markeredgecolor=COLOR_MARKER_FLOOR, markersize=9,
                       label="Marker AprilTag (a pavimento)")
            )
        if high_tags:
            handles.append(
                Line2D([0], [0], marker="s", color="none",
                       markerfacecolor=to_rgba(COLOR_MARKER_HIGH, MARKER_FILL_ALPHA),
                       markeredgecolor=COLOR_MARKER_HIGH, markersize=9,
                       label="Marker AprilTag (in quota)")
            )

    if waypoints:
        if show_yaw:
            handles.append(
                Line2D([0], [0], color=COLOR_YAW, linewidth=1.6, label="Orientamento (yaw)")
            )

    ax.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.05),
        ncol=1,
        fontsize=9,
        framealpha=0.9,
        handler_map=({home_handle: _HandlerHomeMarker()} if home_handle is not None else None),
    )


def _setup_axes(ax, xlim, ylim, title, scenario, coords_shown=False):
    ax.set_title(title, fontsize=15, fontweight="bold", pad=26)
    framing = "coordinate (X, Y, Z)" if coords_shown else "vista d'insieme"
    subtitle = " · ".join(p for p in (scenario, framing) if p)
    if subtitle:
        ax.annotate(
            subtitle,
            xy=(0.5, 1.0), xycoords="axes fraction",
            xytext=(0, 8), textcoords="offset points",
            ha="center", va="bottom", fontsize=9.5, color=PALETTE["sottotitolo"],
            annotation_clip=False,
        )
    if coords_shown:
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.tick_params(labelbottom=False, labelleft=False, length=0)
    else:
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.tick_params(labelsize=10)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.7)
    ax.axhline(0, color=PALETTE["asse_zero"], linewidth=0.6, alpha=0.4)
    ax.axvline(0, color=PALETTE["asse_zero"], linewidth=0.6, alpha=0.4)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)


def _marker_pixel_halfsize(s, dpi):
    return (math.sqrt(s) / 2.0) * dpi / 72.0


def _shrink_overlapping_symbols(fig, ax, symbols):
    if len(symbols) < 2:
        return 1.0

    fig.canvas.draw()
    trans = ax.transData
    origin = trans.transform((0.0, 0.0))
    px_per_unit = abs(trans.transform((1.0, 0.0))[0] - origin[0])
    if px_per_unit <= 0.0:
        return 1.0

    halves = [_marker_pixel_halfsize(s.base_size, fig.dpi) for s in symbols]

    breathing_px = 2.0
    scale = 1.0
    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            dx = symbols[i].x - symbols[j].x
            dy = symbols[i].y - symbols[j].y
            distance_px = math.hypot(dx, dy) * px_per_unit
            if distance_px < 1e-6:
                continue
            needed = halves[i] + halves[j] + breathing_px
            if needed > distance_px:
                scale = min(scale, distance_px / needed)

    scale = max(scale, SYMBOL_MIN_SCALE)
    if scale < 1.0:
        for symbol in symbols:
            symbol.apply_scale(scale)
    return scale


def _rect_overlap_area(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0.0 and h > 0.0) else 0.0


def _segment_hits_rect(p0, p1, rect):
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - rect[0]), (dx, rect[2] - x0),
                 (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0.0:
                return False
            continue
        t = q / p
        if p < 0.0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1


_LABEL_CANDIDATES = (
    (1.0, 0.0, "left", "center"),
    (-1.0, 0.0, "right", "center"),
    (0.0, 1.0, "center", "bottom"),
    (0.0, -1.0, "center", "top"),
    (0.72, 0.72, "left", "bottom"),
    (-0.72, 0.72, "right", "bottom"),
    (0.72, -0.72, "left", "top"),
    (-0.72, -0.72, "right", "top"),
)

_PENALTY_OVERLAP = 3.0
_PENALTY_SEGMENT = 1800.0
_PENALTY_SITE_EDGE = 400.0
_PENALTY_SIDE = 900.0
_PENALTY_ORDER = 90.0


def _label_scene(fig, ax, markers, segments, site_edges, margin_px):
    trans = ax.transData
    dpi = fig.dpi

    origin = trans.transform((0.0, 0.0))
    px_per_unit = abs(trans.transform((1.0, 0.0))[0] - origin[0]) or 1.0

    marker_boxes = []
    own_half = {}
    for (mx, my, s) in markers:
        half = _marker_pixel_halfsize(s, dpi)
        px, py = trans.transform((mx, my))
        marker_boxes.append((px - half - margin_px, py - half - margin_px,
                             px + half + margin_px, py + half + margin_px, mx, my))
        own_half[(round(mx, 6), round(my, 6))] = half

    return {
        "trans": trans,
        "px_per_unit": px_per_unit,
        "marker_boxes": marker_boxes,
        "own_half": own_half,
        "segments": [(trans.transform(p0), trans.transform(p1)) for p0, p1 in segments],
        "site_edges": [(trans.transform(p0), trans.transform(p1)) for p0, p1 in site_edges],
    }


def _crowding_order(owners, trans, marker_boxes):
    def crowding(i):
        ox, oy = owners[i]
        opx, opy = trans.transform((ox, oy))
        reach = 90.0
        return -sum(
            1 for (bx0, by0, bx1, by1, mx, my) in marker_boxes
            if not (abs(mx - ox) < 1e-6 and abs(my - oy) < 1e-6)
            and abs((bx0 + bx1) / 2.0 - opx) < reach
            and abs((by0 + by1) / 2.0 - opy) < reach
        )

    order = list(range(len(owners)))
    order.sort(key=crowding)
    return order


def _label_penalty(box, scene, placed_boxes, owner, order_index, gap_mult, wanted_side, fx):
    ox, oy = owner
    penalty = 0.0
    for (bx0, by0, bx1, by1, mx, my) in scene["marker_boxes"]:
        if abs(mx - ox) < 1e-6 and abs(my - oy) < 1e-6:
            continue
        penalty += _PENALTY_OVERLAP * _rect_overlap_area(box, (bx0, by0, bx1, by1))
    for other in placed_boxes:
        penalty += _PENALTY_OVERLAP * _rect_overlap_area(box, other)
    for p0, p1 in scene["segments"]:
        if _segment_hits_rect(p0, p1, box):
            penalty += _PENALTY_SEGMENT
    for p0, p1 in scene["site_edges"]:
        if _segment_hits_rect(p0, p1, box):
            penalty += _PENALTY_SITE_EDGE
    penalty += _PENALTY_ORDER * order_index
    penalty += 40.0 * (gap_mult - 1.0)
    if wanted_side is not None and fx * wanted_side <= 0.0:
        penalty += _PENALTY_SIDE
    return penalty


def _place_coord_labels(fig, ax, texts, owners, markers, segments, site_edges=(),
                        preferred_sides=None, colliding_first=True):
    if not texts:
        return 0

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    margin_px = 3.0
    scene = _label_scene(fig, ax, markers, segments, site_edges, margin_px)

    order = (
        _crowding_order(owners, scene["trans"], scene["marker_boxes"])
        if colliding_first
        else list(range(len(texts)))
    )

    soglia_libera = _PENALTY_ORDER * (len(_LABEL_CANDIDATES) - 1)

    placed_boxes = []
    leftover = 0
    for i in order:
        text = texts[i]
        ox, oy = owners[i]
        half = scene["own_half"].get((round(ox, 6), round(oy, 6)), 12.0)
        wanted_side = (preferred_sides or {}).get((round(ox, 6), round(oy, 6)))

        best = None
        for gap_mult in (1.0, 1.45, 2.0):
            base_gap = (half + 7.0) * gap_mult
            for order_index, (fx, fy, ha, va) in enumerate(_LABEL_CANDIDATES):
                tx = ox + (fx * base_gap) / scene["px_per_unit"]
                ty = oy + (fy * base_gap) / scene["px_per_unit"]
                text.set_position((tx, ty))
                text.set_ha(ha)
                text.set_va(va)
                bb = text.get_window_extent(renderer=renderer)
                box = (bb.x0 - margin_px, bb.y0 - margin_px,
                       bb.x1 + margin_px, bb.y1 + margin_px)

                penalty = _label_penalty(
                    box, scene, placed_boxes, (ox, oy), order_index, gap_mult,
                    wanted_side, fx,
                )

                if best is None or penalty < best[0]:
                    best = (penalty, tx, ty, ha, va, box)
            if best is not None and best[0] <= soglia_libera:
                break

        _, tx, ty, ha, va, box = best
        text.set_position((tx, ty))
        text.set_ha(ha)
        text.set_va(va)
        placed_boxes.append(box)
        if best[0] > soglia_libera + 40.0:
            leftover += 1

    return leftover



def _scene_bounds(world_tags, waypoints, site_area, restricted_areas):
    area_vertices = [v for area in restricted_areas for v in area]
    xs = (
        [pose.position_m[0] for pose in world_tags.values()]
        + [wp.x for wp in waypoints]
        + [v[0] for v in site_area]
        + [v[0] for v in area_vertices]
    )
    ys = (
        [pose.position_m[1] for pose in world_tags.values()]
        + [wp.y for wp in waypoints]
        + [v[1] for v in site_area]
        + [v[1] for v in area_vertices]
    )
    return xs, ys


def _draw_scene(ax, *, world_tags, waypoints, arrow_length, show_coords, show_yaw,
                supervision_1based, yaw_offset, home_1based):
    coord_texts, coord_owners, coord_segments, symbols = [], [], [], []
    coord_sides = {}

    if world_tags:
        texts, owners, drawn, sides = _draw_markers(ax, world_tags, show_coords)
        coord_texts += texts
        coord_owners += owners
        symbols += drawn
        coord_sides.update(sides)

    if waypoints:
        texts, owners, segments, drawn = _draw_path(
            ax, waypoints, arrow_length, show_coords, show_yaw,
            supervision_1based, yaw_offset, home_1based,
        )
        coord_texts += texts
        coord_owners += owners
        coord_segments += segments
        symbols += drawn

    return coord_texts, coord_owners, coord_segments, symbols, coord_sides


def _fit_and_size(fig, ax, *, xs, ys, span, coord_texts, title, scenario, show_coords):
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    bx = [min(xs), max(xs)]
    by = [min(ys), max(ys)]
    for text in coord_texts:
        bbox = text.get_window_extent(renderer=renderer)
        (x0, y0) = inv.transform((bbox.x0, bbox.y0))
        (x1, y1) = inv.transform((bbox.x1, bbox.y1))
        bx += [x0, x1]
        by += [y0, y1]
    pad = span * 0.05

    def _limiti(valori, minimo_punti, massimo_punti):
        centro = (minimo_punti + massimo_punti) / 2.0
        semi = max(
            max(valori) - centro, centro - min(valori), (massimo_punti - minimo_punti) / 2.0
        ) + pad
        return (centro - semi, centro + semi)

    xlim = _limiti(bx, min(xs), max(xs))
    ylim = _limiti(by, min(ys), max(ys))
    _setup_axes(ax, xlim, ylim, title, scenario, coords_shown=show_coords)
    width_data = xlim[1] - xlim[0]
    height_data = ylim[1] - ylim[0]
    scale = 11.0 / max(width_data, height_data)
    fig.set_size_inches(max(width_data * scale, 7.0), height_data * scale + 1.8)


def _render(
    output_path, show, *, title, scenario, waypoints, supervision_1based, yaw_offset,
    draw_waypoints, draw_markers, show_coords, show_yaw, home_1based=None,
):
    world_tags = APP_CONFIG.camera_pose.world_tags if draw_markers else {}
    waypoints = tuple(waypoints) if draw_waypoints else ()
    site_area = tuple(APP_CONFIG.site_area_vertices_m)
    restricted_areas = tuple(APP_CONFIG.restricted_areas_vertices_m)

    xs, ys = _scene_bounds(world_tags, waypoints, site_area, restricted_areas)
    if not xs:
        print(f"Nessun elemento da disegnare: {output_path.name} non viene generata.")
        return

    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    arrow_length = span * 0.05

    margin = span * 0.18
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_xlim(min(xs) - margin, max(xs) + margin)
    ax.set_ylim(min(ys) - margin, max(ys) + margin)

    site_edges = _draw_site_area(ax, site_area)
    site_edges += _draw_restricted_areas(ax, restricted_areas)

    coord_texts, coord_owners, coord_segments, symbols, coord_sides = _draw_scene(
        ax,
        world_tags=world_tags,
        waypoints=waypoints,
        arrow_length=arrow_length,
        show_coords=show_coords,
        show_yaw=show_yaw,
        supervision_1based=supervision_1based,
        yaw_offset=yaw_offset,
        home_1based=home_1based,
    )

    supervision_shown = bool(
        draw_waypoints
        and set(supervision_1based) & set(range(1, len(waypoints) + 1))
    )
    tag_altitudes = [pose.position_m[2] for pose in world_tags.values()]
    _build_legend(
        ax, markers=draw_markers, waypoints=draw_waypoints, show_yaw=show_yaw,
        home=draw_waypoints and home_1based is not None,
        supervision=supervision_shown,
        floor_tags=any(z < MARKER_HIGH_Z_THRESHOLD for z in tag_altitudes),
        high_tags=any(z >= MARKER_HIGH_Z_THRESHOLD for z in tag_altitudes),
        site_area=bool(site_area),
        restricted_areas=bool(restricted_areas),
    )

    def _adatta():
        _fit_and_size(
            fig, ax, xs=xs, ys=ys, span=span, coord_texts=coord_texts,
            title=title, scenario=scenario, show_coords=show_coords,
        )

    _adatta()
    symbol_scale = _shrink_overlapping_symbols(fig, ax, symbols)
    if symbol_scale < 1.0:
        print(f"  (simboli ridotti al {symbol_scale * 100:.0f}% per non sovrapporsi)")

    marker_geom = [
        (s.x, s.y, s.base_size * symbol_scale * symbol_scale) for s in symbols
    ]

    if coord_texts:
        for _ in range(2):
            leftover = _place_coord_labels(
                fig, ax, coord_texts, coord_owners, marker_geom, coord_segments,
                site_edges, coord_sides,
            )
            _adatta()
        if leftover:
            nome = "etichetta" if leftover == 1 else "etichette"
            print(f"  ({leftover} {nome} senza una posizione del tutto libera)")

    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    print(f"Piantina salvata in: {output_path}")

    if show:
        plt.show()
    plt.close(fig)



def genera_mappe(output_dir, show, *, waypoints, supervision_1based, yaw_offset, label,
                 home_1based=None):
    output_dir.mkdir(parents=True, exist_ok=True)

    common = dict(
        scenario=label,
        waypoints=waypoints,
        supervision_1based=supervision_1based,
        yaw_offset=yaw_offset,
        home_1based=home_1based,
    )

    if waypoints:
        _render(
            output_dir / "map_waypoint_path.png", show,
            title="Percorso dei waypoint",
            draw_waypoints=True, draw_markers=False, show_coords=True, show_yaw=False,
            **common,
        )
    _render(
        output_dir / "map_apriltag_markers.png", show,
        title="Marker AprilTag di riferimento",
        draw_waypoints=False, draw_markers=True, show_coords=True, show_yaw=False,
        **common,
    )
    if waypoints:
        _render(
            output_dir / "map_path_and_markers.png", show,
            title="Percorso e marker AprilTag",
            draw_waypoints=True, draw_markers=True, show_coords=False, show_yaw=True,
            **common,
        )


def _collect_scenarios(only=None):
    scenarios = []
    for path in load_waypoint_paths(APP_CONFIG.waypoint_paths_dir):
        supervision = path.supervision_waypoints or ()
        home_wp = path.home_waypoint
        waypoints = path.waypoints
        home_1based = None
        if home_wp is not None:
            waypoints = waypoints + (home_wp,)
            home_1based = len(waypoints)
        scenarios.append(
            {
                "folder": path.source.stem,
                "label": path.name,
                "waypoints": waypoints,
                "supervision_1based": supervision,
                "home_1based": home_1based,
            }
        )

    if only is not None:
        target = only.strip().lower()
        scenarios = [
            s for s in scenarios
            if target in (s["folder"].lower(), s["label"].lower())
        ]

    return scenarios


def main():
    ensure_utf8_console()
    parser = argparse.ArgumentParser(
        description="Genera le piantine della mappa di volo per ogni scenario di waypoint."
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help="Genera solo lo scenario indicato (stem del file .json o nome del "
             "percorso). Default: tutti gli scenari di waypoint_paths/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=MAPS_DIR,
        help="Cartella base sotto cui creare le sottocartelle per scenario "
             "(default: la cartella 'maps' del package, drone/maps/).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Elenca gli scenari disponibili senza generare nulla.",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Salva i file senza aprire le finestre interattive.",
    )
    args = parser.parse_args()

    scenarios = _collect_scenarios(only=args.scenario)

    if args.list:
        available = _collect_scenarios()
        width = max((len(s["folder"]) for s in available), default=0)
        print("Scenari disponibili:")
        for s in available:
            print(f"  - {s['folder']:<{width}}   {s['label']} ({len(s['waypoints'])} waypoint)")
        return

    if not scenarios:
        available = _collect_scenarios()
        if not available:
            print(
                f"Nessuno scenario trovato in {APP_CONFIG.waypoint_paths_dir}: "
                "aggiungi almeno un file .json di percorso."
            )
            return
        print(f"Nessuno scenario corrisponde a '{args.scenario}'. Scenari disponibili:")
        width = max(len(s["folder"]) for s in available)
        for s in available:
            print(f"  - {s['folder']:<{width}}   {s['label']}")
        return

    yaw_offset = APP_CONFIG.apriltag_autopilot.yaw_offset_deg

    show = (not args.no_show) and len(scenarios) == 1
    if not args.no_show and not show:
        print("Più scenari da generare: le piantine vengono salvate senza aprire le finestre.")

    for s in scenarios:
        output_dir = args.output_dir / s["folder"]
        print(f"\n=== Scenario '{s['label']}' -> {output_dir} ===")
        genera_mappe(
            output_dir=output_dir,
            show=show,
            waypoints=s["waypoints"],
            supervision_1based=s["supervision_1based"],
            yaw_offset=yaw_offset,
            label=s["label"],
            home_1based=s["home_1based"],
        )


if __name__ == "__main__":
    main()
