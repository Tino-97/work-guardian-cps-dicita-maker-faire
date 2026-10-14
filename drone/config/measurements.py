from __future__ import annotations


WORLD_TAGS_RAW: dict[int, dict[str, tuple[float, float, float]]] = {
    0:  {"position_m": (0.0, 0.0, 0.0),    "orientation_rpy_deg": (0.0, 0.0, 0.0)},
    1:  {"position_m": (0.0, -1.8, 0.0),  "orientation_rpy_deg": (0.0, 0.0, 0.0)},
    2:  {"position_m": (-0.3, 2.96, 1.5),  "orientation_rpy_deg": (-90.0, 0.0, 180.0)},
    3:  {"position_m": (-0.3, -5.07, 1.5), "orientation_rpy_deg": (-90.0, 0.0, 0.0)},
    4:  {"position_m": (1.91, 0.0, 1.63),  "orientation_rpy_deg": (-90.0, 0.0, 90.0)},
    5:  {"position_m": (1.91, -2.1, 1.62), "orientation_rpy_deg": (-90.0, 0.0, 90.0)},
    6:  {"position_m": (1.91, -3.9, 1.5),  "orientation_rpy_deg": (-90.0, 0.0, 90.0)},
    7:  {"position_m": (1.45, -5.07, 1.5), "orientation_rpy_deg": (-90.0, 0.0, 0.0)},
    8:  {"position_m": (-2.04, 0.0, 1.48),  "orientation_rpy_deg": (-90.0, 0.0, -90.0)},
    9:  {"position_m": (-2.04, -2.1, 1.49), "orientation_rpy_deg": (-90.0, 0.0, -90.0)},
    10: {"position_m": (-1.8, -1.09, 1.5), "orientation_rpy_deg": (-90.0, 0.0, 180.0)},
    11: {"position_m": (-1.54, -0.9, 1.5), "orientation_rpy_deg": (-90.0, 0.0, -90.0)},
    12: {"position_m": (-1.24, 1.5, 1.49),   "orientation_rpy_deg": (-90.0, 0.0, 180.0)},
    13: {"position_m": (1.7, -0.9, 1.65),  "orientation_rpy_deg": (-90.0, 0.0, 90.0)},
    14: {"position_m": (-1.8, -0.66, 1.5), "orientation_rpy_deg": (-90.0, 0.0, 0.0)},
    15: {"position_m": (0.6, -5.07, 1.51),  "orientation_rpy_deg": (-90.0, 0.0, 0.0)},
    16: {"position_m": (0.6, 2.96, 1.5),   "orientation_rpy_deg": (-90.0, 0.0, 180.0)},
    17: {"position_m": (-1.37, -3.3, 1.5), "orientation_rpy_deg": (-90.0, 0.0, 0.0)},
    18: {"position_m": (-1.15, -4.15, 1.5), "orientation_rpy_deg": (-90.0, 0.0, -90.0)},
}


# Muri della stanza di volo, gli stessi su cui sono appesi i marker 2-9: i
# waypoint e la posizione stimata del drone devono restarne dentro.
FLIGHT_AREA_X_M: tuple[float, float] = (-2.04, 1.91)
FLIGHT_AREA_Y_M: tuple[float, float] = (-5.07, 2.96)


SITE_AREA_VERTICES_M: tuple[tuple[float, float], ...] = (
    (-1.0, 1.0),
    (1.0, 1.0),
    (1.0, -3.0),
    (-1.0, -3.0),
)


RESTRICTED_AREAS_RAW: tuple[dict[str, tuple[float, float]], ...] = (
    {"center_m": (0.45, 0.45), "size_m": (0.4, 0.3)},
)


CAMERA_MATRIX: tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
] = (
    (939.9451542597831, 0.0, 486.5451569111854),
    (0.0, 940.8447164292168, 329.0528996241572),
    (0.0, 0.0, 1.0),
)

DIST_COEFFS: tuple[float, float, float, float, float] = (
    0.02411954416409598,
    -0.18722784189053135,
    -0.011223295008542767,
    -0.0019630367896265257,
    0.5027508681156333,
)
