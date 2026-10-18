from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet


_FONT_NAME = "Arial"

# Palette comune ai report di volo e biometrici. I colori di gruppo servono
# solo nelle intestazioni dei fogli dati: il corpo del file resta neutro.
INK = "243447"
MUTED = "64748B"
NAVY = "284B63"
BLUE = "3F6F8F"
TEAL = "397A78"
PURPLE = "6B648A"
SLATE = "5F6B7A"
AMBER = "9A6B2F"
RED = "9B4A43"
LIGHT_BG = "F7F9FC"
BORDER_COLOR = "D9E0E7"

TITLE_FONT = Font(name=_FONT_NAME, size=18, bold=True, color=INK)
SUBTITLE_FONT = Font(name=_FONT_NAME, size=10, italic=True, color=MUTED)
SECTION_FONT = Font(name=_FONT_NAME, size=11, bold=True, color="FFFFFF")
HEADER_FONT = Font(name=_FONT_NAME, size=10, bold=True, color="FFFFFF")
LABEL_FONT = Font(name=_FONT_NAME, size=10, bold=True, color=INK)
BASE_FONT = Font(name=_FONT_NAME, size=10, color=INK)
SMALL_FONT = Font(name=_FONT_NAME, size=9, color=MUTED)
LINK_FONT = Font(name=_FONT_NAME, size=9, bold=True, color="2F6B9A", underline="single")
CARD_LABEL_FONT = Font(name=_FONT_NAME, size=9, bold=True, color=MUTED)
CARD_VALUE_FONT = Font(name=_FONT_NAME, size=16, bold=True, color=INK)
CARD_VALUE_LIGHT_FONT = Font(name=_FONT_NAME, size=16, bold=True, color="FFFFFF")

HEADER_FILL = PatternFill("solid", fgColor=NAVY)
SECTION_FILL = PatternFill("solid", fgColor=BLUE)
ZEBRA_FILL = PatternFill("solid", fgColor=LIGHT_BG)
CARD_FILL = PatternFill("solid", fgColor="EEF3F7")

# Intestazioni per gruppi logici nei fogli dati.
TIME_HEADER_FILL = PatternFill("solid", fgColor=NAVY)
MEASURE_HEADER_FILL = PatternFill("solid", fgColor=BLUE)
TARGET_HEADER_FILL = PatternFill("solid", fgColor=TEAL)
CONTROL_HEADER_FILL = PatternFill("solid", fgColor=PURPLE)
QUALITY_HEADER_FILL = PatternFill("solid", fgColor=AMBER)
STATUS_HEADER_FILL = PatternFill("solid", fgColor=SLATE)
ALERT_HEADER_FILL = PatternFill("solid", fgColor=RED)

OK_FILL = PatternFill("solid", fgColor="DDF0E3")
ALERT_FILL = PatternFill("solid", fgColor="F5D8D5")
WARN_FILL = PatternFill("solid", fgColor="F8E8C9")
INFO_FILL = PatternFill("solid", fgColor="E8F0F6")
OK_FONT = Font(name=_FONT_NAME, size=10, bold=True, color="25613B")
ALERT_FONT = Font(name=_FONT_NAME, size=10, bold=True, color="8D3029")
WARN_FONT = Font(name=_FONT_NAME, size=10, bold=True, color="7A551A")

CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")
WRAP_LEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)
WRAP_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

_THIN = Side(style="thin", color=BORDER_COLOR)
_MEDIUM = Side(style="medium", color=NAVY)
HEADER_BORDER = Border(bottom=_MEDIUM)
CELL_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
SOFT_BORDER = Border(bottom=_THIN)
CARD_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

FMT_M = "0.000"
FMT_DEG = "0.0"
FMT_S = "0.000"
FMT_PCT = '0.0"%"'
FMT_TIME = "hh:mm:ss"
FMT_INT = "0"

_COL_MIN_WIDTH = 8
_COL_MAX_WIDTH = 38
_COL_PADDING = 3

# Colori delle linguette dei fogli.
TAB_SUMMARY = BLUE
TAB_PARAMETERS = SLATE
TAB_AUTOPILOT = TEAL
TAB_KALMAN = PURPLE
TAB_WATCH = BLUE
TAB_ALARMS = RED
TAB_LEGEND = "7A8794"


def bool_it(value: Any) -> str:
    return "Sì" if bool(value) else "No"


def clock(timestamp: float) -> datetime:
    return datetime.fromtimestamp(float(timestamp))


_REASON_MAP = {
    "idle": "In attesa",
    "tracking": "Inseguimento waypoint",
    "z_priority_tracking": "Correzione di quota",
    "waypoint_reached": "Waypoint raggiunto",
    "waypoint_timeout": "Tempo scaduto sul waypoint",
    "mission_finished": "Missione completata",
    "pose_timeout": "Posizione persa",
    "pose_missing": "Posizione assente",
    "out_of_area": "Posizione fuori dalla stanza",
    "home": "Rientro alla base",
    "supervision_stop": "Sosta di supervisione",
    "supervision_stop_started": "Supervisione avviata",
    "supervision_stop_completed": "Supervisione completata",
    "supervision_stop_tracking": "Supervisione: assestamento",
    "supervision_stop_z_priority_tracking": "Supervisione: quota",
}


def _renamed_reason(raw: str) -> str:
    return raw.replace("supervision_hold", "supervision_stop")


def translate_reason(raw: Any) -> str:
    if raw is None:
        return ""
    s = _renamed_reason(str(raw).strip())
    if s in _REASON_MAP:
        return _REASON_MAP[s]
    if s.startswith("invalid_pose_transient"):
        return "Posizione non valida (temporanea)"
    if s.startswith("invalid_pose"):
        return "Posizione non valida"
    return s


_SOURCE_BASE = {
    "weighted_average": "media pesata dei tag",
    "best_tag": "tag migliore",
    "raw": "grezza",
}


def translate_source(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if s.startswith("kalman(") and s.endswith(")"):
        inner = s[len("kalman("):-1]
        return "Kalman (" + _SOURCE_BASE.get(inner, inner) + ")"
    base = _SOURCE_BASE.get(s, s)
    return base[:1].upper() + base[1:] if base else ""


def _display_length(value: Any, fmt: Optional[str]) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime):
        return 8
    if isinstance(value, (int, float)) and fmt:
        if fmt in (FMT_M, FMT_S):
            return len(f"{float(value):.3f}")
        if fmt in (FMT_DEG, "0.0"):
            return len(f"{float(value):.1f}")
        if fmt == FMT_PCT:
            return len(f"{float(value):.1f}") + 1
        if fmt == FMT_INT:
            return len(f"{int(round(float(value)))}")
    return len(str(value))


def autofit_columns(
    ws: Worksheet,
    *,
    min_width: int = _COL_MIN_WIDTH,
    max_width: int = _COL_MAX_WIDTH,
    padding: int = _COL_PADDING,
) -> None:
    """Autofit controllato: evita colonne enormi causate da intestazioni lunghe."""
    merged: set[str] = set()
    for cell_range in ws.merged_cells.ranges:
        for row in ws[cell_range.coord]:
            for cell in row:
                merged.add(cell.coordinate)

    widths: dict[str, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None or cell.coordinate in merged:
                continue
            length = max(len(part) for part in str(cell.value).split("\n"))
            if not isinstance(cell.value, str):
                length = _display_length(cell.value, cell.number_format)
            column = cell.column_letter
            if length > widths.get(column, 0):
                widths[column] = length

    for column, length in widths.items():
        ws.column_dimensions[column].width = min(
            max(length + padding, min_width), max_width
        )


def setup_sheet(
    ws: Worksheet,
    *,
    tab_color: Optional[str] = None,
    zoom: int = 90,
    show_gridlines: bool = False,
) -> None:
    ws.sheet_view.showGridLines = show_gridlines
    ws.sheet_view.zoomScale = zoom
    if tab_color:
        ws.sheet_properties.tabColor = tab_color
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.45
    ws.page_margins.bottom = 0.45
    ws.page_margins.header = 0.2
    ws.page_margins.footer = 0.2


def configure_print(
    ws: Worksheet,
    *,
    orientation: str = "landscape",
    fit_to_width: int = 1,
    repeat_rows: Optional[str] = None,
) -> None:
    ws.page_setup.orientation = orientation
    ws.page_setup.fitToWidth = fit_to_width
    ws.page_setup.fitToHeight = 0
    if repeat_rows:
        ws.print_title_rows = repeat_rows


def report_title(
    ws: Worksheet,
    title: str,
    subtitle: str,
    *,
    span: int = 6,
) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = TITLE_FONT
    title_cell.alignment = LEFT
    ws.row_dimensions[1].height = 28

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    subtitle_cell = ws.cell(row=2, column=1, value=subtitle)
    subtitle_cell.font = SUBTITLE_FONT
    subtitle_cell.alignment = WRAP_LEFT
    ws.row_dimensions[2].height = 26
    return 4


def add_back_link(
    ws: Worksheet,
    *,
    row: int = 3,
    text: str = "← Torna al Riepilogo",
) -> None:
    cell = ws.cell(row=row, column=1, value=text)
    cell.hyperlink = "#'Riepilogo'!A1"
    cell.font = LINK_FONT
    cell.alignment = LEFT


def prepare_summary_sheet(ws: Worksheet, title: str, subtitle: str) -> int:
    setup_sheet(ws, tab_color=TAB_SUMMARY, zoom=95)
    configure_print(ws, orientation="portrait", fit_to_width=1)
    report_title(ws, title, subtitle, span=6)
    # Le righe descrittive usano A:D per l'etichetta ed E:F per il valore.
    # Si lascia più spazio alle etichette: i nomi sono volutamente descrittivi
    # e devono restare leggibili senza invadere la colonna del valore.
    for col in ("A", "B", "C", "D"):
        ws.column_dimensions[col].width = 17.5
    for col in ("E", "F"):
        ws.column_dimensions[col].width = 14
    ws.freeze_panes = "A4"
    return 4


def metric_card(
    ws: Worksheet,
    row: int,
    start_col: int,
    label: str,
    value: Any,
    *,
    number_format: Optional[str] = None,
    fill: Optional[PatternFill] = None,
    value_font: Optional[Font] = None,
) -> None:
    end_col = start_col + 1
    ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
    ws.merge_cells(start_row=row + 1, start_column=start_col, end_row=row + 1, end_column=end_col)

    label_cell = ws.cell(row=row, column=start_col, value=label.upper())
    label_cell.font = CARD_LABEL_FONT
    label_cell.alignment = WRAP_CENTER

    value_cell = ws.cell(row=row + 1, column=start_col, value=value)
    value_cell.font = value_font or CARD_VALUE_FONT
    value_cell.alignment = CENTER
    if number_format:
        value_cell.number_format = number_format

    selected_fill = fill or CARD_FILL
    for rr in (row, row + 1):
        for cc in range(start_col, end_col + 1):
            cell = ws.cell(row=rr, column=cc)
            cell.fill = selected_fill
            cell.border = CARD_BORDER
    ws.row_dimensions[row].height = 26
    ws.row_dimensions[row + 1].height = 30


def section_title(ws: Worksheet, row: int, text: str, span: int = 6) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = SECTION_FONT
    cell.fill = SECTION_FILL
    cell.alignment = LEFT
    ws.row_dimensions[row].height = 22
    return row + 1


def kv(
    ws: Worksheet,
    row: int,
    label: str,
    value: Any,
    fmt: Optional[str] = None,
    *,
    label_end_col: int = 4,
    value_start_col: int = 5,
    value_end_col: int = 6,
) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=label_end_col)
    ws.merge_cells(
        start_row=row,
        start_column=value_start_col,
        end_row=row,
        end_column=value_end_col,
    )
    label_cell = ws.cell(row=row, column=1, value=label)
    label_cell.font = LABEL_FONT
    label_cell.alignment = WRAP_LEFT
    value_cell = ws.cell(row=row, column=value_start_col, value=value)
    value_cell.font = BASE_FONT
    if fmt is not None:
        value_cell.number_format = fmt
        value_cell.alignment = RIGHT
    else:
        value_cell.alignment = WRAP_LEFT

    for col in range(1, value_end_col + 1):
        ws.cell(row=row, column=col).border = SOFT_BORDER

    # Altezza adattiva per evitare sovrapposizioni con etichette descrittive o
    # valori testuali lunghi (orari, cause di arresto, ecc.).
    longest = max(len(str(label)), len(str(value)) if value is not None else 0)
    if longest > 70:
        ws.row_dimensions[row].height = 44
    elif longest > 44:
        ws.row_dimensions[row].height = 32
    else:
        ws.row_dimensions[row].height = 20
    return row + 1


def table_header(ws: Worksheet, row: int, headers: list[str]) -> int:
    for col_index, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col_index, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = WRAP_CENTER
        cell.border = CELL_BORDER
    ws.row_dimensions[row].height = 30
    return row + 1


def fmt_num_it(value: float, decimals: int = 3) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")
