"""Finestra della demo, nello stile dell'interfaccia di WORK-GUARDIAN.

Come la schermata iniziale del gruppo (drone/ui/setup/welcome.py), tutte le
misure sono riferite a uno schermo 1920x1080 e la grafica viene ridisegnata
alla risoluzione reale della finestra: testi e riquadri restano proporzionati
e leggibili su qualunque monitor, senza che OpenCV stiri l'immagine.

Disposizione: banner del progetto in alto; sotto, a sinistra, il video con i
riconoscimenti e, a destra, "Stato orologio" e "Log degli alert" nello stile
dei pannelli dell'interfaccia di volo (drone/ui/video/panels.py). Le parti
statiche sono disegnate una volta sola per dimensione della finestra; i testi
dei pannelli solo quando cambiano.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw

REF_W, REF_H = 1920, 1080

# Colori dell'interfaccia (drone/ui/setup/effects.py e drone/ui/video/panels.py).
CARD_EDGE_ON = (8, 214, 246)
CYAN_BRIGHT = (16, 226, 250)
WHITE = (248, 251, 252)
HEADER_FILL = (3, 26, 50)
HEADER_EDGE = (6, 160, 230)
HEADER_SEP = (4, 110, 170)
TITLE_ACCENT = (150, 238, 252)
LOG_FILL = (3, 27, 48)
LOG_EDGE = (4, 150, 225)
LOG_RULE = (4, 104, 156)
TEXT = (232, 242, 250)
LABEL = (206, 244, 252)
MUTED = (140, 162, 182)
POS = (88, 226, 120)
NEG = (252, 98, 88)
WARN = (252, 190, 16)
ACCENT = (110, 238, 252)
GRAY_BGR = (150, 150, 150)
ZONE_BGR = (40, 40, 230)
TAG_BGR = (255, 200, 0)

GLYPH_SHAPES = {"·": "info", "•": "info", "!": "avviso", "×": "allerta", "✓": "esito"}
GLYPH_COLUMN, MESSAGE_COLUMN = 10, 13

# --------------------------------------------------- elementi del progetto

try:
    from drone.ui import fonts
except Exception:  # pragma: no cover - versione del progetto senza fonts.py
    from PIL import ImageFont

    class fonts:  # noqa: N801 - stessa interfaccia del modulo del progetto
        @staticmethod
        def _load(names, size):
            for name in names:
                try:
                    return ImageFont.truetype(name, size)
                except OSError:
                    continue
            return ImageFont.load_default()

        sans = staticmethod(lambda s: fonts._load(("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"), s))
        sans_bold = staticmethod(lambda s: fonts._load(("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"), s))
        mono = staticmethod(lambda s: fonts._load(("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"), s))

try:
    from drone.ui.shapes import glow_box
except Exception:  # pragma: no cover
    def glow_box(img, box, *, radius, edge=None, width=0.0, fill=None, glow=0.0, glow_alpha=0):
        ImageDraw.Draw(img).rounded_rectangle(
            box, radius=int(radius), fill=fill[:3] if fill else None,
            outline=tuple(edge[:3]) if edge else None, width=max(1, int(round(width))))

try:
    from drone.ui.setup.effects import grid_background
except Exception:  # pragma: no cover
    def grid_background(w, h, step, arm, **_kw):
        return Image.new("RGB", (w, h), (4, 27, 50))

try:
    from drone.ui.appearance import ASSETS_DIR, LOGO_GLOB, PROJECT_TITLE
except Exception:  # pragma: no cover
    ASSETS_DIR, LOGO_GLOB, PROJECT_TITLE = Path("."), "banner*", "WORK GUARDIAN"

try:
    from drone.config import APP_CONFIG
    _THR = APP_CONFIG.smartwatch_thresholds
    BPM_RANGE = (_THR.bpm_min_in, _THR.bpm_max_in)
    SPO2_MIN = _THR.spo2_min_in
except Exception:  # pragma: no cover
    BPM_RANGE, SPO2_MIN = (50, 120), 92


def enable_dpi_awareness() -> None:
    """Come l'interfaccia di volo: senza, Windows ingrandisce la finestra
    sfocandola sugli schermi con ridimensionamento al 125% o al 150%."""
    try:
        from drone.ui.video.window import enable_high_dpi_awareness
        enable_high_dpi_awareness()
        return
    except Exception:
        pass
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _banner_source() -> Optional[Image.Image]:
    """Banner della schermata iniziale, senza i margini bianchi laterali."""
    try:
        path = next(p for p in sorted(Path(ASSETS_DIR).glob(LOGO_GLOB)) if p.is_file())
        with Image.open(path) as src:
            source = src.convert("RGB")
    except (StopIteration, OSError):
        return None
    diff = ImageChops.difference(source, Image.new("RGB", source.size, (255, 255, 255)))
    bounds = diff.convert("L").point(lambda p: 255 if p > 8 else 0).getbbox()
    if bounds is not None:
        pad = max(1, round(source.height * 0.04))
        source = source.crop((max(0, bounds[0] - pad), 0,
                              min(source.width, bounds[2] + pad), source.height))
    return source


# ------------------------------------------------------------- disposizione

@dataclass
class Layout:
    W: int
    H: int
    k: float
    banner: tuple
    header: tuple
    video_card: tuple
    video: tuple  # x, y, larghezza, altezza dell'immagine
    watch: Optional[tuple]
    status: Optional[tuple]
    log: tuple

    def s(self, v: float) -> int:
        return int(round(v * self.k))

    def content(self, box: tuple) -> tuple:
        """Area dei testi di un pannello, sotto titolo e riga."""
        x0, y0, x1, y1 = box
        return (x0 + self.s(13), y0 + self.s(53), x1 - self.s(13), y1 - self.s(10))


def compute_layout(W: int, H: int, aspect: float, watch_on: bool, status_on: bool,
                   banner_ratio: float) -> Layout:
    k = max(0.5, min(2.5, min(W / REF_W, H / REF_H)))
    s = lambda v: int(round(v * k))  # noqa: E731
    m, g = s(18), s(14)
    inset = s(6)
    bh = int(min((W - 2 * m - 2 * inset) * banner_ratio + 2 * inset, 0.16 * H))
    banner = (m, m, W - m, m + bh)
    top, bottom = banner[3] + g, H - m
    header_h, vin, col_min = s(48), s(6), s(470)
    avail_h = bottom - top - header_h - s(8) - 2 * vin
    avail_w = W - 2 * m - g - col_min - 2 * vin
    vh = max(10, avail_h)
    vw = int(vh * aspect)
    if vw > avail_w:
        vw = max(10, avail_w)
        vh = int(vw / aspect)
    header = (m, top, m + vw + 2 * vin, top + header_h)
    vtop = header[3] + s(8)
    video_card = (m, vtop, m + vw + 2 * vin, vtop + vh + 2 * vin)
    video = (m + vin, vtop + vin, vw, vh)
    x0, x1, col_bottom = video_card[2] + g, W - m, video_card[3]
    y, watch, status = top, None, None
    if watch_on:
        watch = (x0, y, x1, y + s(214))
        y = watch[3] + g
    if status_on:
        status = (x0, y, x1, y + int((col_bottom - y) * 0.68))
        y = status[3] + g
    return Layout(W, H, k, banner, header, video_card, video, watch, status,
                  (x0, y, x1, max(y + s(120), col_bottom)))


# ---------------------------------------------------------------- disegno

def _bgr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"))[:, :, ::-1].copy()


def _bgra(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGBA"))[:, :, [2, 1, 0, 3]].copy()


def blend(canvas: np.ndarray, x: int, y: int, rgba: np.ndarray) -> None:
    """Sovrappone un'immagine BGRA con trasparenza."""
    h, w = rgba.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.shape[1], x + w), min(canvas.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    patch = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = patch[:, :, 3:4].astype(np.float32) / 255.0
    region = canvas[y0:y1, x0:x1]
    region[:] = (region * (1.0 - a) + patch[:, :, :3] * a).astype(np.uint8)


def _wrap(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    out, cur = [], ""
    for word in text.split(" "):
        while len(word) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            out.append(cur)
            cur = word
    out.append(cur)
    return out


def _log_pieces(lines, max_chars: int, max_rows: int, newest_last: bool = True):
    """Righe visibili; le righe lunghe vanno a capo allineate al messaggio,
    come nei log dell'interfaccia di volo. In un log restano le più recenti,
    in un elenco (newest_last=False) le prime."""
    rows: list[tuple[str, bool, str]] = []
    ordered = list(lines)
    if not newest_last:
        for line in ordered:
            pieces = _split_line(line, max_chars)
            if len(rows) + len(pieces) > max_rows:
                break
            rows.extend((p, ind, line) for p, ind in pieces)
        return rows
    for line in reversed(ordered):
        pieces = _split_line(line, max_chars)
        if len(rows) + len(pieces) > max_rows:
            if rows:
                break
            pieces = pieces[:max_rows]
        rows[0:0] = [(p, ind, line) for p, ind in pieces]
    return rows


def _split_line(line: str, max_chars: int) -> list[tuple[str, bool]]:
    if len(line) <= max_chars:
        return [(line, False)]
    head, msg = line[:MESSAGE_COLUMN], line[MESSAGE_COLUMN:]
    parts = _wrap(msg, max(8, max_chars - MESSAGE_COLUMN))
    return [(head + parts[0], False)] + [(p, True) for p in parts[1:]]


def _glyph(d: ImageDraw.ImageDraw, shape: str, cx: float, cy: float, r: float, color) -> None:
    if shape == "avviso":
        t = r * 1.15
        d.polygon([(cx, cy - t), (cx + t, cy + t * 0.78), (cx - t, cy + t * 0.78)], fill=color)
    elif shape == "allerta":
        w, a = max(1, round(r * 0.6)), r * 0.9
        d.line((cx - a, cy - a, cx + a, cy + a), fill=color, width=w)
        d.line((cx - a, cy + a, cx + a, cy - a), fill=color, width=w)
    elif shape == "esito":
        w = max(1, round(r * 0.5))
        d.line((cx - r, cy + r * 0.1, cx - r * 0.2, cy + r * 0.8), fill=color, width=w)
        d.line((cx - r * 0.2, cy + r * 0.8, cx + r, cy - r * 0.8), fill=color, width=w)
    else:
        m = r * 0.55
        d.ellipse((cx - m, cy - m, cx + m, cy + m), fill=color)


def line_color(line: str):
    glyph = line[GLYPH_COLUMN] if len(line) > GLYPH_COLUMN else ""
    return {"×": NEG, "!": WARN, "•": ACCENT}.get(glyph, TEXT)


def render_lines(lines, w: int, h: int, k: float, empty_note: str = "",
                 newest_last: bool = True) -> np.ndarray:
    """Testo di un pannello in stile log (carattere a spaziatura fissa,
    simbolo di gravità colorato), disegnato al doppio e poi ridotto."""
    ss = 2
    img = Image.new("RGB", (w * ss, h * ss), LOG_FILL)
    d = ImageDraw.Draw(img)
    font = fonts.mono(max(8, round(18 * k * ss)))
    line_h = max(10, round(25 * k * ss))
    char_w = float(font.getlength("0"))
    max_chars = max(10, int(w * ss // char_w))
    rows = _log_pieces(lines, max_chars, max(1, h * ss // line_h), newest_last)
    if not rows and empty_note:
        note = fonts.sans(max(8, round(18 * k * ss)))
        d.text((0, line_h / 2), empty_note, font=note, fill=MUTED, anchor="lm")
    y = 0
    for piece, indented, line in rows:
        x = MESSAGE_COLUMN * char_w if indented else 0
        color = line_color(line)
        shape = None if indented else GLYPH_SHAPES.get(piece[GLYPH_COLUMN:GLYPH_COLUMN + 1])
        if shape:
            piece = piece[:GLYPH_COLUMN] + " " + piece[GLYPH_COLUMN + 1:]
        cy = y + line_h / 2
        d.text((x, cy), piece, font=font, fill=color, anchor="lm")
        if shape:
            _glyph(d, shape, x + (GLYPH_COLUMN + 0.5) * char_w, cy, char_w * 0.42, color)
        y += line_h
    return _bgr(img.resize((w, h), Image.LANCZOS))


def _heart(d, cx, cy, s, color) -> None:
    r = 3.0 * s
    for dx in (-2.9, 2.9):
        ex, ey = cx + dx * s, cy - 1.6 * s
        d.ellipse((ex - r, ey - r, ex + r, ey + r), fill=color)
    d.polygon([(cx - 5.8 * s, cy - 0.9 * s), (cx + 5.8 * s, cy - 0.9 * s), (cx, cy + 6.2 * s)],
              fill=color)


def _drop(d, cx, cy, s, color) -> None:
    r, by = 4.2 * s, cy + 2.0 * s
    d.ellipse((cx - r, by - r, cx + r, by + r), fill=color)
    d.polygon([(cx - 3.7 * s, by - 2.0 * s), (cx + 3.7 * s, by - 2.0 * s), (cx, cy - 7.0 * s)],
              fill=color)


def render_watch(state: dict, w: int, h: int, k: float) -> np.ndarray:
    """Contenuto di "Stato orologio": collegamento, battito e saturazione, in
    verde se nella norma e in rosso fuori dalle soglie dell'orologio."""
    ss = 2
    s = k * ss
    img = Image.new("RGB", (w * ss, h * ss), LOG_FILL)
    d = ImageDraw.Draw(img)
    connected, bpm, spo2 = state.get("connected"), state.get("bpm"), state.get("spo2")
    y = round(14 * s)
    r = round(7 * s)
    d.ellipse((0, y - r, 2 * r, y + r), fill=POS if connected else NEG)
    label = "Collegato" if connected else (state.get("note") or "In attesa dell'orologio")
    d.text((2 * r + round(12 * s), y), label,
           font=fonts.sans(round(20 * s)), fill=LABEL if connected else MUTED, anchor="lm")

    values = []
    for draw_icon, value, text, bad in (
        (_heart, bpm, "-- bpm" if bpm is None else f"{bpm} bpm",
         bpm is not None and not BPM_RANGE[0] <= bpm <= BPM_RANGE[1]),
        (_drop, spo2, "SpO2 --" if spo2 is None else f"SpO2 {spo2}%",
         spo2 is not None and spo2 < SPO2_MIN),
    ):
        values.append((draw_icon, text, MUTED if value is None else (NEG if bad else POS),
                       MUTED if value is None else (NEG if bad else WHITE)))
    # Battito e saturazione affiancati se c'è spazio, altrimenti uno sotto
    # l'altro; il carattere si riduce se l'altezza non basta.
    top, H2, W2 = round(34 * s), h * ss, w * ss
    icon_w, gap = round(30 * s), round(12 * s)
    size = round(38 * s)
    big = fonts.sans_bold(size)
    widths = [icon_w + gap + big.getlength(t) for _i, t, _c, _tc in values]
    side_by_side = sum(widths) + round(40 * s) <= W2
    rows = 1 if side_by_side else 2
    row_h = (H2 - top) / rows
    if size > row_h * 0.72:
        size = max(8, int(row_h * 0.72))
        big = fonts.sans_bold(size)
    for i, (draw_icon, text, icon_color, text_color) in enumerate(values):
        if side_by_side:
            x, cy = (0 if i == 0 else int(W2 / 2)), top + row_h / 2
        else:
            x, cy = 0, top + row_h * (i + 0.5)
        draw_icon(d, x + icon_w / 2, cy, 2.3 * s * min(1.0, size / (38 * s)), icon_color)
        d.text((x + icon_w + gap, cy), text, font=big, fill=text_color, anchor="lm")
    return _bgr(img.resize((w, h), Image.LANCZOS))


def render_header(title: str, detail: str, detail_warn: bool, w: int, h: int, k: float) -> np.ndarray:
    """Intestazione del video, come quella della mappa nell'interfaccia di
    volo: titolo, separatore e dettaglio."""
    ss = 2
    s = k * ss
    img = Image.new("RGB", (w * ss, h * ss), HEADER_FILL)
    d = ImageDraw.Draw(img)
    font = fonts.sans(round(21 * s))
    cy = h * ss / 2
    x = 0
    d.text((x, cy), title, font=font, fill=TITLE_ACCENT, anchor="lm")
    if detail:
        x += d.textlength(title, font=font) + round(12 * s)
        d.text((x, cy), "|", font=font, fill=HEADER_SEP, anchor="lm")
        x += d.textlength("|", font=font) + round(12 * s)
        d.text((x, cy), detail, font=font, fill=WARN if detail_warn else LABEL, anchor="lm")
    return _bgr(img.resize((w, h), Image.LANCZOS))


def render_pill(key: str, text: str, k: float) -> np.ndarray:
    """Conferma di un comando, come i suggerimenti dell'interfaccia di volo."""
    ss = 2
    s = k * ss
    fk, ft = fonts.sans_bold(round(17 * s)), fonts.sans(round(17 * s))
    pad, h, gap = round(16 * s), round(42 * s), round(12 * s)
    key_w = max(int(fk.getlength(key)) + round(14 * s), round(26 * s))
    w = pad + key_w + gap + int(ft.getlength(text)) + pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=(3, 27, 45, 226),
                        outline=(4, 180, 236, 235), width=max(1, round(s)))
    cy = h // 2
    d.rounded_rectangle((pad, cy - round(13 * s), pad + key_w, cy + round(13 * s)),
                        radius=round(7 * s), fill=(16, 226, 250, 255))
    d.text((pad + key_w / 2, cy), key, font=fk, fill=(3, 20, 40), anchor="mm")
    d.text((pad + key_w + gap, cy), text, font=ft, fill=(206, 244, 252), anchor="lm")
    return _bgra(img.resize((max(1, w // ss), max(1, h // ss)), Image.LANCZOS))


def render_alarm(text: str, k: float) -> np.ndarray:
    """Banner rosso della rete mancante, come quello dell'interfaccia di volo."""
    ss = 2
    s = k * ss
    font = fonts.sans_bold(round(23 * s))
    bh, pad = round(62 * s), round(8 * s)
    text_x = round(69 * s)
    bw = text_x + int(font.getlength(text)) + round(26 * s)
    img = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
    glow_box(img, (pad, pad, pad + bw - 1, pad + bh - 1), radius=9 * s, edge=(255, 128, 116),
             width=3.2 * s, fill=(150, 30, 36, 246), glow=pad / 3, glow_alpha=150)
    d = ImageDraw.Draw(img)
    cx, cy, r = pad + round(36 * s), pad + bh / 2, 13 * s
    d.polygon([(cx, cy - r * 0.98), (cx - r * 1.12, cy + r * 0.98), (cx + r * 1.12, cy + r * 0.98)],
              fill=(255, 255, 255))
    ex = max(2, round(2.6 * s * 1.2))
    d.line((cx, cy - r * 0.24, cx, cy + r * 0.34), fill=(150, 30, 36), width=ex)
    d.ellipse((cx - ex * 0.7, cy + r * 0.50, cx + ex * 0.7, cy + r * 0.50 + ex * 1.5),
              fill=(150, 30, 36))
    d.text((pad + text_x, cy), text, font=font, fill=(255, 255, 255), anchor="lm")
    return _bgra(img.resize((max(1, img.width // ss), max(1, img.height // ss)), Image.LANCZOS))


def draw_detections(img: np.ndarray, result: dict, styles: dict) -> None:
    """Riquadri dei riconoscimenti sul video mostrato, come
    overlay.draw_cached_detections: colore del modello, nome e confidenza.
    Le persone ignorate dal filtro (e i loro DPI) restano in grigio."""
    fh, fw = result["shape"]
    sx, sy = img.shape[1] / fw, img.shape[0] / fh
    k = max(0.5, img.shape[0] / 720)
    font, fs, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.6 * k, max(1, round(2 * k))
    corridor = result.get("corridor")
    if corridor:
        x1, x2 = int(corridor[0] * sx), int(corridor[1] * sx)
        shade = img.copy()
        shade[:, :x1] = 0
        shade[:, x2:] = 0
        cv2.addWeighted(shade, 0.45, img, 0.55, 0, img)
    for tag_id, corners in result.get("tags") or []:
        pts = np.array([(c[0] * sx, c[1] * sy) for c in corners], np.int32)
        cv2.polylines(img, [pts], True, TAG_BGR, thick, cv2.LINE_AA)
        cv2.putText(img, f"tag {tag_id}", (int(pts[0][0]), int(pts[0][1]) - 6), font, fs * 0.8,
                    TAG_BGR, thick, cv2.LINE_AA)
    ignored = result.get("ignored", ())
    for entry in result["detections"]:
        style = styles.get(entry["name"])
        for det in entry["detections"]:
            if det.get("manual"):
                continue
            active = id(det) not in ignored
            x1, y1, x2, y2 = (int(det["bbox"][0] * sx), int(det["bbox"][1] * sy),
                              int(det["bbox"][2] * sx), int(det["bbox"][3] * sy))
            color = style["color"] if (active and style) else GRAY_BGR
            name = style["classes"].get(det["label"], det["label"]) if style else det["label"]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, thick if active else 1, cv2.LINE_AA)
            cv2.putText(img, f"{name} {round(det['confidence'] * 100)}%",
                        (x1, max(int(20 * k), y1 - int(10 * k))), font,
                        fs if active else fs * 0.8, color, thick if active else 1, cv2.LINE_AA)


def draw_zone(img: np.ndarray, zone: tuple, frame_shape: tuple) -> None:
    """Zona vietata disegnata col mouse (coordinate dell'immagine analizzata)."""
    sx, sy = img.shape[1] / frame_shape[1], img.shape[0] / frame_shape[0]
    k = max(0.5, img.shape[0] / 720)
    thick = max(1, round(2 * k))
    z = (int(zone[0] * sx), int(zone[1] * sy), int(zone[2] * sx), int(zone[3] * sy))
    cv2.rectangle(img, z[:2], z[2:], ZONE_BGR, thick, cv2.LINE_AA)
    cv2.putText(img, "Zona vietata", (z[0] + 6, z[1] + int(24 * k)), cv2.FONT_HERSHEY_SIMPLEX,
                0.6 * k, ZONE_BGR, thick, cv2.LINE_AA)


# ----------------------------------------------------------------- finestra

@dataclass
class ViewState:
    """Quello che la finestra deve mostrare in un dato istante."""
    frame: Optional[np.ndarray]
    placeholder: str
    result: Optional[dict]
    styles: dict
    zone: Optional[tuple]
    title: str
    detail: str
    detail_warn: bool
    watch: Optional[dict]
    log: list = field(default_factory=list)
    status_title: str = ""
    status: Optional[list] = None
    toast: Optional[tuple] = None
    net_missing: bool = False


class Screen:
    def __init__(self, title: str, fullscreen: bool = False,
                 on_mouse: Optional[Callable] = None) -> None:
        self.title = title
        self.on_mouse = on_mouse
        self.layout: Optional[Layout] = None
        self._layout_key = None
        self._bg: Optional[np.ndarray] = None
        self._cache: dict = {}
        self._size = None
        self._pending = (None, 0.0)
        self._aspect = 16 / 9
        self._frame_shape = None
        self._banner = _banner_source()
        self._banner_ratio = (self._banner.height / self._banner.width) if self._banner else 0.09
        self.fullscreen = fullscreen
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.imshow(title, np.zeros((720, 1280, 3), np.uint8))
        cv2.waitKey(1)
        if fullscreen:
            cv2.setWindowProperty(title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            self._maximize()
        cv2.setMouseCallback(title, self._mouse)

    # ------------------------------------------------------------ finestra
    def _maximize(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, self.title)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        except Exception:
            pass
        cv2.waitKey(1)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN if self.fullscreen else cv2.WINDOW_NORMAL)
        if not self.fullscreen:
            self._maximize()

    def closed(self) -> bool:
        try:
            return cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    def _window_size(self) -> tuple[int, int]:
        """Dimensione dell'area della finestra. Durante un ridimensionamento
        si aspetta che la misura si fermi prima di ridisegnare tutto."""
        try:
            _x, _y, w, h = cv2.getWindowImageRect(self.title)
        except cv2.error:
            w = h = -1
        if w < 320 or h < 240:
            return self._size or (REF_W, REF_H)
        now = time.monotonic()
        if self._size is None:
            self._size = (w, h)
        elif (w, h) != self._size:
            if self._pending[0] != (w, h):
                self._pending = ((w, h), now)
            elif now - self._pending[1] > 0.2:
                self._size = (w, h)
        return self._size

    def _mouse(self, event, x, y, _flags, _param) -> None:
        if self.on_mouse is None or self.layout is None or self._frame_shape is None:
            return
        vx, vy, vw, vh = self.layout.video
        fh, fw = self._frame_shape
        fx = min(max(x - vx, 0), vw) * fw / vw
        fy = min(max(y - vy, 0), vh) * fh / vh
        inside = vx <= x < vx + vw and vy <= y < vy + vh
        self.on_mouse(event, fx, fy, inside)

    # -------------------------------------------------------------- disegno
    def _background(self, L: Layout, panel_titles: dict) -> np.ndarray:
        img = grid_background(L.W, L.H, L.s(64), L.s(6))
        if img.mode != "RGB":
            img = img.convert("RGB")
        k = L.k
        # Banner come nella schermata iniziale: bordo luminoso, immagine
        # centrata su fondo bianco, filo ciano interno.
        glow_box(img, L.banner, radius=3 * k, edge=CARD_EDGE_ON, width=2 * k, glow=8 * k,
                 glow_alpha=150)
        x0, y0, x1, y1 = L.banner
        inset = L.s(6)
        draw = ImageDraw.Draw(img)
        draw.rectangle((x0 + inset, y0 + inset, x1 - inset, y1 - inset), fill=(255, 255, 255))
        if self._banner is not None:
            bw, bh = x1 - x0 - 2 * inset, y1 - y0 - 2 * inset
            scale = min(bw / self._banner.width, bh / self._banner.height)
            size = (max(1, int(self._banner.width * scale)), max(1, int(self._banner.height * scale)))
            banner = self._banner.resize(size, Image.LANCZOS)
            img.paste(banner, (x0 + inset + (bw - size[0]) // 2, y0 + inset + (bh - size[1]) // 2))
        else:
            draw.text(((x0 + x1) / 2, (y0 + y1) / 2), PROJECT_TITLE,
                      font=fonts.sans_bold(L.s(46)), fill=(10, 40, 80), anchor="mm")
        edge = 4 * k
        draw.rectangle((x0 + edge, y0 + edge, x1 - edge, y1 - edge), outline=CYAN_BRIGHT,
                       width=max(1, round(1.6 * k)))
        # Intestazione e riquadro del video.
        glow_box(img, L.header, radius=9 * k, edge=HEADER_EDGE, width=2 * k, fill=HEADER_FILL,
                 glow=5 * k, glow_alpha=120)
        glow_box(img, L.video_card, radius=9 * k, edge=LOG_EDGE, width=2 * k, fill=(0, 0, 0),
                 glow=5 * k, glow_alpha=120)
        # Pannelli a destra, come "Log degli alert" dell'interfaccia di volo.
        title_font = fonts.sans_bold(L.s(22))
        for name in ("watch", "status", "log"):
            box = getattr(L, name)
            if box is None:
                continue
            glow_box(img, box, radius=9 * k, edge=LOG_EDGE, width=2 * k, fill=LOG_FILL,
                     glow=5 * k, glow_alpha=120)
            bx0, by0, bx1, _ = box
            draw = ImageDraw.Draw(img)
            draw.text((bx0 + L.s(13), by0 + L.s(25)), panel_titles.get(name, ""), font=title_font,
                      fill=WHITE, anchor="lm")
            draw.line((bx0 + L.s(10), by0 + L.s(45), bx1 - L.s(10), by0 + L.s(45)),
                      fill=LOG_RULE, width=max(1, L.s(2)))
        return _bgr(img)

    def _cached(self, key, make):
        value = self._cache.get(key)
        if value is None:
            value = make()
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = value
        return value

    @staticmethod
    def _paste(canvas: np.ndarray, box: tuple, patch: np.ndarray) -> None:
        x0, y0 = box[0], box[1]
        h, w = patch.shape[:2]
        canvas[y0:y0 + h, x0:x0 + w] = patch

    def render(self, v: ViewState) -> None:
        if v.frame is not None:
            self._aspect = v.frame.shape[1] / v.frame.shape[0]
            self._frame_shape = v.frame.shape[:2]
        W, H = self._window_size()
        watch_on, status_on = v.watch is not None, v.status is not None
        key = (W, H, round(self._aspect, 3), watch_on, status_on, v.status_title)
        if key != self._layout_key:
            self.layout = compute_layout(W, H, self._aspect, watch_on, status_on, self._banner_ratio)
            self._bg = self._background(self.layout, {
                "watch": "Stato orologio", "status": v.status_title, "log": "Log degli alert"})
            self._layout_key = key
            self._cache.clear()
        L = self.layout
        k = L.k
        canvas = self._bg.copy()

        # Intestazione del video.
        hx0, hy0, hx1, hy1 = L.header
        hbox = (hx0 + L.s(18), hy0 + L.s(4), hx1 - L.s(18), hy1 - L.s(4))
        self._paste(canvas, hbox, self._cached(
            ("header", v.title, v.detail, v.detail_warn),
            lambda: render_header(v.title, v.detail, v.detail_warn,
                                  hbox[2] - hbox[0], hbox[3] - hbox[1], k)))

        # Video con i riconoscimenti.
        vx, vy, vw, vh = L.video
        if v.frame is not None:
            interp = cv2.INTER_AREA if vw < v.frame.shape[1] else cv2.INTER_LINEAR
            video = cv2.resize(v.frame, (vw, vh), interpolation=interp)
            if v.result is not None:
                draw_detections(video, v.result, v.styles)
            if v.zone:
                draw_zone(video, v.zone, v.frame.shape[:2])
            canvas[vy:vy + vh, vx:vx + vw] = video
        else:
            note = self._cached(("note", v.placeholder, vw), lambda: _bgra(_note_image(
                v.placeholder, k)))
            blend(canvas, vx + (vw - note.shape[1]) // 2, vy + (vh - note.shape[0]) // 2, note)
        if v.net_missing:
            alarm = self._cached(("alarm",), lambda: render_alarm("Rete di sicurezza mancante", k))
            blend(canvas, vx + (vw - alarm.shape[1]) // 2, vy + L.s(14), alarm)
        if v.toast:
            pill = self._cached(("pill",) + tuple(v.toast), lambda: render_pill(v.toast[0], v.toast[1], k))
            blend(canvas, vx + (vw - pill.shape[1]) // 2, vy + vh - pill.shape[0] - L.s(20), pill)

        # Pannelli a destra.
        if L.watch is not None:
            box = L.content(L.watch)
            st = (v.watch.get("connected"), v.watch.get("bpm"), v.watch.get("spo2"),
                  v.watch.get("note"))
            self._paste(canvas, box, self._cached(("watch",) + st, lambda: render_watch(
                v.watch, box[2] - box[0], box[3] - box[1], k)))
        if L.status is not None:
            box = L.content(L.status)
            self._paste(canvas, box, self._cached(("status", tuple(v.status)), lambda: render_lines(
                v.status, box[2] - box[0], box[3] - box[1], k, newest_last=False)))
        box = L.content(L.log)
        self._paste(canvas, box, self._cached(("log", tuple(v.log)), lambda: render_lines(
            v.log, box[2] - box[0], box[3] - box[1], k, "Gli allarmi compariranno qui")))

        cv2.imshow(self.title, canvas)


def _note_image(text: str, k: float) -> Image.Image:
    font = fonts.sans(max(10, round(24 * k)))
    w = int(font.getlength(text)) + 8
    h = round(40 * k)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((4, h / 2), text, font=font, fill=MUTED + (255,), anchor="lm")
    return img
