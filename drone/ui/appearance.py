from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_TITLE: str = "WORK-GUARDIAN"

ASSETS_DIR: Path = Path(__file__).resolve().parent / "assets"

LOGO_GLOB: str = "banner_welcome.*"

WINDOW_TITLE: str = "WORK-GUARDIAN - Preparazione al volo"

SCENARIO_WINDOW_TITLE: str = "WORK-GUARDIAN - Scelta dello scenario"

VIDEO_WINDOW_TITLE: str = "WORK-GUARDIAN - Volo e supervisione"

CONTROLLER_WINDOW_TITLE: str = "WORK-GUARDIAN - Controller"


@dataclass(frozen=True)
class DashboardConfig:
    enabled: bool = True

    panel_width: int = 940

    log_row_min_height: int = 200

    gap_px: int = 6

    video_origin: tuple[int, int] = (0, 0)
    video_size: tuple[int, int] = (960, 960)

    map_title: str = "Mappa missione"
    terminal_title: str = "Terminale drone"
    alerts_title: str = "Log degli alert"

    map_info_col_width: int = 470

    max_lines: int = 500
