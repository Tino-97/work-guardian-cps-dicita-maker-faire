"""Demo da banco di WORK-GUARDIAN con webcam, senza drone.

Lo script mostra al pubblico la parte di visione artificiale del sistema:
i modelli YOLO di vision/models analizzano il flusso di una webcam (o di un
video registrato) e la stessa logica di sorveglianza usata in volo
(drone/surveillance) decide gli allarmi. Gli allarmi possono essere inviati
all'orologio tramite MQTT con lo stesso formato usato dal drone.

Rispetto al sistema di volo cambia una sola cosa: vengono considerate solo le
persone più vicine alla webcam (per default 2). Tutte le altre restano
disegnate in grigio ma non generano allarmi, così il passaggio dei visitatori
sullo sfondo non tiene il sistema in allarme continuo.

Avvio dalla radice del repository:

    python demo/demo_webcam.py
    python demo/demo_webcam.py --mqtt 127.0.0.1
    python demo/demo_webcam.py --sorgente video.mp4

Tutti i parametri si possono modificare anche durante l'esecuzione da
tastiera: premere H per l'elenco dei comandi.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import os

# Nasconde i messaggi interni di OpenCV mentre si cercano le webcam.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from ultralytics import YOLO  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drone.surveillance.alarm_latch import AlarmLatch  # noqa: E402
from drone.surveillance.dpi_monitor import DpiMonitor  # noqa: E402
from drone.surveillance.person_monitor import PersonMonitor  # noqa: E402

# Grafica dell'interfaccia di volo (titolo, pannello orologio, pannelli dei
# log): la demo la riusa così com'è. Se per qualche motivo non si carica, la demo
# disegna una versione semplificata.
try:
    from drone.config import APP_CONFIG  # noqa: E402
    from drone.ui.video import overlay  # noqa: E402
    from drone.ui.video import panels  # noqa: E402
    from drone.ui.video.stdout_redirect import StdoutRedirect  # noqa: E402
    PROJECT_TITLE = APP_CONFIG.project_title
except Exception as exc:  # pragma: no cover
    print(f"Grafica dell'interfaccia non disponibile ({exc}): uso quella semplificata")
    overlay = None
    panels = None
    StdoutRedirect = None
    PROJECT_TITLE = "WORK GUARDIAN"

VIDEO_HEIGHT = 720   # altezza del video nella finestra
PANEL_WIDTH = 940    # colonna dei pannelli, larga come nell'interfaccia di volo
PANEL_GAP = 6
LOGO_FILE = Path(__file__).resolve().parent / "logo.png"

def fit_window(window: str, width: int, height: int) -> None:
    """Dimensiona la finestra perché stia tutta sullo schermo (con un
    margine per barra delle applicazioni e titolo), mantenendo le proporzioni."""
    max_w, max_h = 1600, 900
    try:
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32
            max_w = int(user32.GetSystemMetrics(0) * 0.95)
            max_h = int(user32.GetSystemMetrics(1) * 0.85)
    except Exception:
        pass
    k = min(max_w / width, max_h / height, 1.0)
    cv2.resizeWindow(window, int(width * k), int(height * k))

MODELS_DIR = ROOT / "vision" / "models"
WINDOW = "WORK-GUARDIAN - demo"

PERSON_MODEL = "Caduta_delle_Persone"
AREA_MODEL = "Aree_Interdette"
NET_MODEL = "Protezioni_Collettive"
DPI_MODEL = "Protezioni_Individuali"
PERSON_LABELS = {"person", "fall"}

# Stessa corrispondenza di drone/flight/steps.py: valore del campo "tipo"
# riconosciuto dal firmware dell'orologio. La caduta non va all'orologio.
WATCH_TYPES = {
    "dpi_missing": "DPI_MANCANTE",
    "dpi_missing_update": "DPI_MANCANTE",
    "dpi_ok": "DPI_OK",
    "restricted_area": "AREA_VIETATA",
    "restricted_area_ok": "AREA_OK",
}
LEVELS = {"restricted_area": "critical", "fall": "critical"}

TOPIC_ALARMS = "cantiere/allarmi"
TOPIC_WATCH = "cantiere/sensori/orologio"

GRAY = (150, 150, 150)
RED = (40, 40, 230)
WHITE = (255, 255, 255)

HELP = [
    "H  torna allo stato della demo",
    "S, M  sorveglianza; missione orologio",
    "1-4  modelli DPI/persone/aree/reti",
    "+, -  soglia di confidenza",
    "P, A  persone considerate; altezza min.",
    "T, R  corridoio AprilTag; ricalcolo",
    "Mouse  sx: zona vietata, dx: cancella",
    "W, C  visuale webcam/drone; altra webcam",
    "F, Q  schermo intero; esci (anche Esc)",
]


# --------------------------------------------------------------------- modelli

def load_model(name: str) -> dict | None:
    """Carica un modello del catalogo di vision/models."""
    catalog = json.loads((MODELS_DIR / "catalog.json").read_text(encoding="utf-8"))
    for entry in catalog["models"]:
        if Path(entry["weights"]).parent.parent.name != name:
            continue
        print(f"Caricamento di {name}...")
        return {
            "yolo": YOLO(str(MODELS_DIR / entry["weights"])),
            "color": tuple(entry.get("color", (0, 255, 0))),
            "classes": entry.get("classes", {}),
            "label": entry.get("label", name),
            "enabled": True,
        }
    print(f"Modello {name} non trovato in vision/models/catalog.json")
    return None


def load_models(names: list[str]) -> dict:
    models = {}
    for name in names:
        m = load_model(name)
        if m is not None:
            models[name] = m
    return models


def inference_device():
    """GPU NVIDIA se PyTorch la vede, altrimenti CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            print(f"Inferenza su GPU: {name}")
            return 0, "GPU"
    except Exception:
        pass
    print("Inferenza su CPU (nessuna GPU disponibile per PyTorch): la demo sarà più lenta. "
          "Con una scheda NVIDIA va installato PyTorch con CUDA, vedi demo/info.txt")
    return "cpu", "CPU"


def detect(models: dict, frame, conf: float, imgsz: int, device="cpu") -> list[dict]:
    """Stesso formato di drone/perception/object_detector.py."""
    out = []
    for name, m in models.items():
        if not m["enabled"]:
            continue
        result = m["yolo"].predict(frame, conf=conf, imgsz=imgsz, device=device,
                                   half=device != "cpu", verbose=False)[0]
        dets = []
        for box in result.boxes:
            label = result.names[int(box.cls[0])]
            dets.append({
                "label": label,
                "confidence": float(box.conf[0]),
                "bbox": tuple(float(v) for v in box.xyxy[0]),
            })
        out.append({"name": name, "detections": dets})
    return out


# ------------------------------------------------------- filtro sulle persone

def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(b, person, margin=0.10):
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    w, h = person[2] - person[0], person[3] - person[1]
    return (person[0] - margin * w <= cx <= person[2] + margin * w
            and person[1] - margin * h <= cy <= person[3] + margin * h)


def select_people(dets: list[dict], max_people: int, min_height: float, frame_h: int,
                  corridor: tuple[float, float] | None = None):
    """Tiene le persone più grandi (le più vicine) e le rilevazioni che cadono
    dentro di loro. Con le AprilTag visibili considera solo chi ha i piedi tra
    il tag più a sinistra e quello più a destra. Aree interdette e reti non
    dipendono dalle persone e passano sempre. Se il modello delle persone è
    spento non filtra nulla."""
    people = [
        d["bbox"] for e in dets if e["name"] == PERSON_MODEL
        for d in e["detections"] if d["label"].casefold() in PERSON_LABELS
        and (d["bbox"][3] - d["bbox"][1]) >= min_height * frame_h
        and (corridor is None or corridor[0] <= (d["bbox"][0] + d["bbox"][2]) / 2 <= corridor[1])
    ]
    if not any(e["name"] == PERSON_MODEL for e in dets):
        return dets, []
    kept = sorted(people, key=_area, reverse=True)[:max_people]

    filtered = []
    for e in dets:
        if e["name"] in (AREA_MODEL, NET_MODEL):
            filtered.append(e)
            continue
        keep = [d for d in e["detections"] if any(_inside(d["bbox"], p) for p in kept)]
        filtered.append({"name": e["name"], "detections": keep})
    return filtered, kept


# -------------------------------------------------------------------- AprilTag

class Tags:
    """AprilTag sulle barriere verticali, come in laboratorio.

    Il corridoio tra il tag più a sinistra e quello più a destra delimita la
    scena: resta memorizzato anche quando una persona copre i tag, finché non
    vengono rivisti o finché non si preme R."""

    def __init__(self, family: str, margin: float):
        self.margin = margin
        self.visible: list[tuple[int, list]] = []
        self.corridor: tuple[float, float] | None = None
        self.detector = None
        try:
            from pyapriltags import Detector
            self.detector = Detector(families=family)
        except Exception as exc:
            print(f"AprilTag non disponibili ({exc}): corridoio disattivato")

    def update(self, frame) -> None:
        if self.detector is None:
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.visible = [(int(d.tag_id), d.corners.tolist()) for d in self.detector.detect(gray)]
        xs = [c[0] for _id, corners in self.visible for c in corners]
        if len(self.visible) >= 2:
            pad = self.margin * frame.shape[1]
            self.corridor = (max(0.0, min(xs) - pad), min(frame.shape[1], max(xs) + pad))

    def reset(self) -> None:
        self.corridor = None


# ------------------------------------------------------------------------ MQTT

class Mqtt:
    """Client opzionale: se il broker non c'è la demo funziona lo stesso."""

    def __init__(self, host: str | None, port: int):
        self.client = None
        self.connected = False
        # L'orologio ignora gli allarmi fuori missione: con il broker la demo
        # parte già in missione (tasto M per terminarla).
        self.mission = bool(host)
        self.watch = None  # ultima telemetria dell'orologio
        self.on_alert = None  # funzione (simbolo, messaggio) per il log degli alert
        if not host:
            return
        try:
            import paho.mqtt.client as mqtt
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wg-demo")
            except AttributeError:  # paho-mqtt 1.x
                self.client = mqtt.Client(client_id="wg-demo")
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.client.connect_async(host, port)
            self.client.loop_start()
        except Exception as exc:
            print(f"MQTT non disponibile ({exc}): la demo prosegue senza orologio")
            self.client = None

    def _on_connect(self, client, *_args):
        self.connected = True
        client.subscribe(TOPIC_WATCH)
        self._send_mission()

    def _on_disconnect(self, *_args):
        self.connected = False

    def _on_message(self, _c, _u, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if "lettura_valida" in data or ("bpm" in data and "evento" not in data):
            self.watch = {"t": time.monotonic(), **data}
        # Come nell'interfaccia di volo: l'allarme biometrico dell'orologio
        # finisce nel log degli alert.
        if data.get("evento") == "BIOMETRIA_ANOMALA" and self.on_alert is not None:
            bpm = "--" if data.get("bpm") is None else data.get("bpm")
            spo2 = "--" if data.get("spo2") is None else f"{data.get('spo2')}%"
            self.on_alert(GLYPH_WARN, f"Orologio: {bpm} bpm, SpO2 {spo2}")

    def _publish(self, payload: dict, retain=False):
        if self.client is not None:
            self.client.publish(TOPIC_ALARMS, json.dumps(payload), qos=1, retain=retain)

    def _send_mission(self):
        tipo = "AVVIO_MISSIONE" if self.mission else "FINE_MISSIONE"
        self._publish({"source": "drone", "tipo": tipo}, retain=True)

    def set_mission(self, active: bool):
        self.mission = active
        self._send_mission()

    def alarm(self, alert: dict):
        kind = alert.get("type", "surveillance")
        payload = {
            "source": "drone",
            "type": kind,
            "level": LEVELS.get(kind, "warning"),
            "msg": alert.get("message", ""),
        }
        if kind in WATCH_TYPES:
            payload["tipo"] = WATCH_TYPES[kind]
        if alert.get("missing"):
            payload["dettaglio"] = ", ".join(n.capitalize() for n in alert["missing"])
        self._publish(payload)

    def close(self):
        if self.client is None:
            return
        self.set_mission(False)
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()


# ---------------------------------------------------------------------- webcam

def _backends():
    if sys.platform == "win32":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    return [cv2.CAP_ANY]


def _try_open(index, backend, fmt, reads=15):
    """Apre una webcam con un driver e un formato e restituisce
    (cap, luminosità media, variazione tra fotogrammi) oppure (None, 0, 0)."""
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None, 0.0, 0.0
    if fmt:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    brightness, change, prev = 0.0, 0.0, None
    for _ in range(reads):  # i primi fotogrammi possono essere scuri
        ok, frame = cap.read()
        if ok and frame is not None:
            brightness = max(brightness, float(frame.mean()))
            small = cv2.resize(frame, (160, 90)).astype("float32")
            if prev is not None:
                change = max(change, float(abs(small - prev).mean()))
            prev = small
        time.sleep(0.03)
    return cap, brightness, change


def scan_webcams(max_index: int = 5) -> list[dict]:
    """Controllo rapido di tutte le webcam collegate. Per ognuna cerca la
    configurazione che dà immagine e misura luminosità e variazione tra un
    fotogramma e l'altro: una webcam vera ha sempre un po' di rumore, mentre
    una fotocamera virtuale ferma (per esempio con un logo fisso) manda
    sempre la stessa immagine. Restituisce prima le webcam vere, dalla più
    luminosa alla meno luminosa, e in fondo quelle virtuali."""
    print("Ricerca delle webcam...")
    found = []
    for index in range(max_index):
        best = None
        for backend in _backends():
            for fmt in ("MJPG", None):
                cap, brightness, change = _try_open(index, backend, fmt)
                if cap is None:
                    continue
                name = cap.getBackendName()
                cap.release()
                live = brightness > 8 and change > 0.05
                score = (live, brightness)
                if best is None or score > best["score"]:
                    best = {"indice": index, "backend": backend, "nome": name,
                            "formato": fmt, "luminosita": brightness,
                            "variazione": change, "vera": live, "score": score}
                if live and brightness > 20:
                    break
            if best and best["vera"] and best["luminosita"] > 20:
                break
        if best is None:
            continue
        if best["luminosita"] <= 8:
            tipo = "immagine nera, scartata"
        elif not best["vera"]:
            tipo = "immagine ferma, probabilmente una fotocamera virtuale"
        else:
            tipo = "ok"
        print(f"  webcam {index}: luminosità {best['luminosita']:.1f}, "
              f"variazione {best['variazione']:.2f} ({best['nome']}) - {tipo}")
        if best["luminosita"] > 8:
            found.append(best)
    found.sort(key=lambda c: c["score"], reverse=True)
    if found and not found[0]["vera"]:
        print("ATTENZIONE: nessuna webcam vera trovata, uso un'immagine ferma. "
              "Se una webcam è collegata, chiudere le altre app che la usano.")
    return found


def open_candidate(c: dict):
    cap, _, _ = _try_open(c["indice"], c["backend"], c["formato"], reads=5)
    if cap is None:
        return None
    ok, frame = cap.read()
    h, w = frame.shape[:2] if ok else (0, 0)
    print(f"Uso la webcam {c['indice']} ({c['nome']}, {w}x{h}) - tasto C per cambiarla")
    return cap


def find_webcam():
    """Usa la webcam con l'immagine più luminosa: su molti PC la 0 è una
    fotocamera virtuale o un ingresso che manda solo nero."""
    cams = scan_webcams()
    for c in cams:
        cap = open_candidate(c)
        if cap is not None:
            return cams, cams.index(c), cap
    sys.exit(
        "Nessuna webcam manda immagini. Controllare che non sia usata da "
        "un'altra app (Teams, browser, Zoom), che in Impostazioni > Privacy > "
        "Fotocamera sia consentito l'accesso alle app desktop e che la webcam "
        "non sia coperta."
    )


def open_source(source, required: bool = True):
    """Apre un video o una webcam. Su Windows il driver predefinito (MSMF) con
    alcune webcam si apre ma non manda immagini: si prova prima DirectShow e si
    controlla che arrivi davvero un fotogramma."""
    if isinstance(source, str):
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            sys.exit(f"Impossibile aprire il video {source}")
        return cap

    backends = [cv2.CAP_ANY]
    if sys.platform == "win32":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    only_black = None
    for backend in backends:
        for fmt in ("MJPG", None):
            cap = cv2.VideoCapture(source, backend)
            if not cap.isOpened():
                cap.release()
                continue
            # Con DirectShow molte webcam mandano immagini nere finché non si
            # imposta esplicitamente formato e risoluzione.
            if fmt:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            got_frame = False
            for _ in range(40):  # alcune webcam impiegano un attimo a partire
                ok, frame = cap.read()
                if ok and frame is not None:
                    got_frame = True
                    if frame.mean() > 8:  # non è un'immagine nera
                        h, w = frame.shape[:2]
                        print(f"Webcam {source} aperta ({cap.getBackendName()}, "
                              f"{w}x{h}{', ' + fmt if fmt else ''})")
                        return cap
                time.sleep(0.05)
            name = cap.getBackendName()
            if required:
                print(f"Webcam {source}: immagini nere con {name}"
                      f"{' ' + fmt if fmt else ''}, provo un'altra configurazione")
            if got_frame and only_black is None:
                only_black = (backend, fmt)
            cap.release()
    if not required:
        return None
    if only_black is not None:
        print("ATTENZIONE: la webcam manda solo immagini nere. Controllare che "
              "non sia coperta (copriobiettivo o tasto fotocamera sulla tastiera) "
              "e che non sia usata da un'altra app.")
        backend, fmt = only_black
        cap = cv2.VideoCapture(source, backend)
        if fmt:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
        return cap
    sys.exit(
        f"La webcam {source} non manda immagini. Controllare che non sia usata da "
        "un'altra app (Teams, browser, Zoom), che in Impostazioni > Privacy > "
        "Fotocamera sia consentito l'accesso alle app desktop, oppure provare "
        "un'altra webcam con --sorgente 1"
    )


# ---------------------------------------------------------------------- disegno

def text(img, s, org, color=WHITE, scale=0.55, thick=1):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _banner(img, i, msg, top=None):
    h, w = img.shape[:2]
    if overlay is not None:
        try:
            if top is None:
                top = overlay.project_title_bottom_edge() + 10
            bw, bh = min(560, w - 20), 62
            by = top + i * (bh + 14)
            overlay._render_safety_net_banner(
                img, (w - bw) // 2, by, bw, bh, (150, 30, 36), (255, 128, 116),
                "warn", msg, True)
            return
        except Exception:
            pass
    y = 90 + 44 * i
    cv2.rectangle(img, (0, y - 28), (w, y + 10), RED, -1)
    text(img, msg.upper(), (12, y), WHITE, 0.8, 2)


_TOAST_CACHE = {}


def draw_toast(img, key_label: str, msg: str) -> None:
    """Conferma a schermo dell'ultimo tasto premuto, nello stile dei
    suggerimenti dell'interfaccia di volo."""
    h, w = img.shape[:2]
    if overlay is not None:
        try:
            k = (key_label, msg)
            pill = _TOAST_CACHE.get(k)
            if pill is None:
                pill = overlay._build_scenario_hint(key_label, msg)
                _TOAST_CACHE.clear()
                _TOAST_CACHE[k] = pill
            x0, y0 = (w - pill.width) // 2, h - pill.height - 20
            if x0 >= 0:
                overlay._composite_rgba(img, x0, y0, pill)
                return
        except Exception:
            pass
    text(img, f"[{key_label}] {msg}", (w // 2 - 200, h - 30), WHITE, 0.7, 2)


def draw_view(img, scale, view, models, zone=None, label=None, right=False):
    """Disegna i riconoscimenti di una sorgente sulla sua immagine mostrata
    (img), ridimensionata di "scale" rispetto a quella analizzata."""
    S = lambda v: int(v * scale)  # noqa: E731
    raw, filtered, kept = view["raw"], view["filtered"], view["kept"]
    kept_ids = {id(d) for e in filtered for d in e["detections"]}
    h, w = img.shape[:2]
    corridor = view.get("corridor")
    if corridor:
        x1, x2 = S(corridor[0]), S(corridor[1])
        shade = img.copy()
        cv2.rectangle(shade, (0, 0), (x1, h), (0, 0, 0), -1)
        cv2.rectangle(shade, (x2, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(shade, 0.45, img, 0.55, 0, img)
    for tag_id, corners in view.get("tags") or []:
        pts = [(S(c[0]), S(c[1])) for c in corners]
        for i in range(4):
            cv2.line(img, pts[i], pts[(i + 1) % 4], (255, 200, 0), 2)
        text(img, f"tag {tag_id}", (pts[0][0], pts[0][1] - 6), (255, 200, 0), 0.5)
    for e in raw:
        m = models.get(e["name"])
        for d in e["detections"]:
            if d.get("manual"):
                continue
            x1, y1, x2, y2 = (S(v) for v in d["bbox"])
            active = id(d) in kept_ids or e["name"] in (AREA_MODEL, NET_MODEL)
            color = m["color"] if (active and m) else GRAY
            name = m["classes"].get(d["label"], d["label"]) if m else d["label"]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2 if active else 1)
            text(img, f"{name} {round(d['confidence'] * 100)}%",
                 (x1 + 3, max(15, y1 - 8)), color, 0.6 if active else 0.45, 2 if active else 1)
    for p in kept:
        x1, y1, x2, y2 = (S(v) for v in p)
        cv2.rectangle(img, (x1, y1), (x2, y2), WHITE, 1)
    if zone:
        z = [S(v) for v in zone]
        cv2.rectangle(img, (z[0], z[1]), (z[2], z[3]), RED, 2)
        text(img, "Zona vietata", (z[0] + 4, z[1] + 18), RED)
    if label:
        if right:
            lw = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0]
            text(img, label, (w - lw - 12, h - 16), (236, 180, 4), 0.7, 2)
        else:
            text(img, label, (12, h - 16), (236, 180, 4), 0.7, 2)


def draw_hud(img, watch, mqtt_on, net_missing=False):
    """Grafica sul video come nell'interfaccia di volo: titolo, pannello
    dell'orologio e, se manca la rete, il banner della verifica delle reti."""
    h, w = img.shape[:2]
    if overlay is None:
        text(img, PROJECT_TITLE, (w // 2 - 110, 40), (236, 180, 4), 1.1, 2)
        if mqtt_on and watch and time.monotonic() - watch["t"] < 3.0:
            text(img, f"Orologio  BPM {watch.get('bpm', '-')}  SpO2 {watch.get('spo2', '-')}",
                 (w - 360, 40), (90, 220, 90), 0.65, 2)
        if net_missing:
            _banner(img, 0, "Rete di sicurezza mancante")
        return
    try:
        overlay.draw_project_title(img, PROJECT_TITLE)
        if mqtt_on:
            fresh = watch is not None and time.monotonic() - watch["t"] < 3.0
            overlay.draw_watch_overlay(img, {
                "connected": fresh,
                "bpm": watch.get("bpm") if fresh else None,
                "spo2": watch.get("spo2") if fresh else None,
            })
        if net_missing:
            overlay.draw_safety_net_verdict_banner(img, {"outcome": "missing"})
    except Exception:
        pass


def letterbox(img, window: str):
    """Adatta l'immagine alle proporzioni della finestra aggiungendo bande
    nere, invece di lasciarla deformare a OpenCV. Restituisce l'immagine e lo
    spostamento delle bande, che serve per riportare le coordinate del mouse."""
    try:
        _x, _y, ww, wh = cv2.getWindowImageRect(window)
    except Exception:
        return img, (0, 0)
    if ww <= 0 or wh <= 0:
        return img, (0, 0)
    h, w = img.shape[:2]
    target = ww / wh
    if abs(w / h - target) < 0.01:
        return img, (0, 0)
    if w / h > target:  # finestra più alta: bande sopra e sotto
        pad = int(round(w / target)) - h
        top = pad // 2
        return cv2.copyMakeBorder(img, top, pad - top, 0, 0, cv2.BORDER_CONSTANT), (0, top)
    pad = int(round(h * target)) - w  # finestra più larga: bande ai lati
    left = pad // 2
    return cv2.copyMakeBorder(img, 0, 0, left, pad - left, cv2.BORDER_CONSTANT), (left, 0)


# ------------------------------------------------------------ colonna destra

def log_line(glyph: str, message: str) -> str:
    """Riga nel formato dei log dell'interfaccia di volo (console.print_event):
    ora, simbolo di gravità, messaggio."""
    return f"{time.strftime('%H:%M:%S')}  {glyph}  {message}"


GLYPH_INFO, GLYPH_WARN, GLYPH_ALERT, GLYPH_NET = "·", "!", "×", "•"


class SidePanel:
    """Colonna a destra del video, costruita con i pannelli dell'interfaccia
    di volo (drone/ui/video/panels.py): in alto il logo e lo stato della
    demo al posto della mappa, sotto il terminale e il log degli alert."""

    def __init__(self, width: int = PANEL_WIDTH, gap: int = PANEL_GAP):
        from collections import deque
        self.width, self.gap = width, gap
        self.terminal = deque(maxlen=300)
        self.alerts = deque(maxlen=300)
        self._cache = {}
        self._dirty = True
        self._logo = None
        if LOGO_FILE.exists():
            self._logo = cv2.imread(str(LOGO_FILE), cv2.IMREAD_UNCHANGED)

    def log_terminal(self, line: str) -> None:
        line = str(line).rstrip()
        if not line:
            return
        if len(line) <= 10 or line[2] != ":" or line[10] not in "·!×•":
            line = log_line(GLYPH_INFO, line)
        self.terminal.append(line)
        self._dirty = True

    def log_alert(self, glyph: str, message: str) -> None:
        self.alerts.append(log_line(glyph, message))
        self._dirty = True

    def _bg(self):
        return np.array((panels.BG if panels else (3, 27, 51))[::-1], dtype=np.uint8)

    def _logo_card(self, size: int):
        key = ("logo", size)
        if key in self._cache:
            return self._cache[key]
        card = np.full((size, size, 3), self._bg(), dtype=np.uint8)
        try:
            from PIL import Image
            img, _d = panels._new_canvas(size, size)
            panels._card_box(img, (8, 2, size - 3, size - 14))
            card = panels._finalize(img)
        except Exception:
            pass
        if self._logo is not None:
            inner = size - 40
            logo = cv2.resize(self._logo, (inner, inner), interpolation=cv2.INTER_AREA)
            x0, y0 = (size - inner) // 2 + 2, (size - 12 - inner) // 2
            roi = card[y0:y0 + inner, x0:x0 + inner].astype(np.float32)
            if logo.shape[2] == 4:
                a = logo[:, :, 3:4].astype(np.float32) / 255.0
                roi = roi * (1 - a) + logo[:, :, :3].astype(np.float32) * a
            else:
                roi = logo.astype(np.float32)
            card[y0:y0 + inner, x0:x0 + inner] = roi.astype(np.uint8)
        self._cache[key] = card
        return card

    def _text(self, title, lines, w, h, color, inset):
        if panels is None:
            img = np.full((h, w, 3), self._bg(), dtype=np.uint8)
            cv2.putText(img, title, (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, WHITE, 2)
            for i, line in enumerate(list(lines)[-max(1, (h - 60) // 22):]):
                cv2.putText(img, line, (14, 60 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1)
            return img
        return panels.text_panel(None, title, list(lines), h, engaged=True,
                                 line_color=color, width=w, inset=inset)

    def render(self, height: int, status_lines, status_title: str) -> np.ndarray:
        g, pw = self.gap, self.width
        top_h = max(250, int(height * 0.42))
        log_h = height - g - top_h
        logo_w = top_h
        status_w = pw - g - logo_w
        left_w = (pw - g) // 2
        right_w = pw - g - left_w
        inset_l = getattr(panels, "LOG_INSET_LEFT", (20, 2, 5, 14))
        inset_r = getattr(panels, "LOG_INSET_RIGHT", (5, 2, 13, 14))
        status_color = panels.alert_line_color if panels else None
        bg = self._bg()

        key = ("status", height, tuple(status_lines), status_title)
        if key != self._cache.get("status_key"):
            self._cache["status_key"] = key
            self._cache["status"] = self._text(status_title, status_lines, status_w, top_h,
                                               status_color, (5, 2, 13, 14))
        if self._dirty or self._cache.get("logs_h") != height:
            self._dirty = False
            self._cache["logs_h"] = height
            term = self._text("Terminale demo", self.terminal, left_w, log_h,
                              panels.terminal_line_color if panels else None, inset_l)
            alerts = self._text("Log degli alert", self.alerts, right_w, log_h,
                                panels.alert_line_color if panels else None, inset_r)
            self._cache["logs"] = np.hstack((term, np.full((log_h, g, 3), bg, np.uint8), alerts))

        top = np.hstack((self._logo_card(logo_w), np.full((top_h, g, 3), bg, np.uint8),
                         self._cache["status"]))
        return np.vstack((top, np.full((g, pw, 3), bg, np.uint8), self._cache["logs"]))


# ------------------------------------------------------------------- sorgenti

class WebcamSource:
    """Webcam (o file video). Con più webcam il tasto C passa alla successiva."""

    def __init__(self, sorgente: str):
        self.label = "WEBCAM"
        if sorgente == "auto":
            self.cams, self.pos, self.cap = find_webcam()
            self.index = self.cams[self.pos]["indice"]
        else:
            self.cams, self.pos = [], 0
            self.index = int(sorgente) if sorgente.isdigit() else sorgente
            self.cap = open_source(self.index)
            if isinstance(self.index, str):
                self.label = "VIDEO"
        self.failed = 0

    def read(self):
        ok, frame = self.cap.read()
        if not ok and isinstance(self.index, str):  # video finito: riparte
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        self.failed = 0 if ok else self.failed + 1
        return frame if ok else None

    def next_camera(self) -> bool:
        if len(self.cams) <= 1:
            return False
        self.cap.release()
        self.pos = (self.pos + 1) % len(self.cams)
        self.cap = open_candidate(self.cams[self.pos]) or self.cap
        self.index = self.cams[self.pos]["indice"]
        return True

    def close(self):
        self.cap.release()


class DroneSource:
    """Camera del Tello, letta con djitellopy come nel sistema di volo. Il PC
    deve essere collegato alla rete Wi-Fi del drone. Il drone non decolla."""

    def __init__(self):
        from djitellopy import Tello
        self.label = "DRONE"
        self.tello = Tello()
        self.tello.connect()
        try:
            print(f"Drone collegato, batteria {self.tello.get_battery()}%")
        except Exception:
            print("Drone collegato")
        try:
            self.tello.streamoff()
        except Exception:
            pass
        self.tello.streamon()
        self.reader = self.tello.get_frame_read()
        self.rgb = True
        if overlay is not None:
            self.rgb = bool(getattr(APP_CONFIG, "frame_from_controller_is_rgb", True))
        self.failed = 0

    def read(self):
        frame = getattr(self.reader, "frame", None)
        if frame is None or getattr(frame, "ndim", 0) != 3:
            self.failed += 1
            return None
        self.failed = 0
        frame = frame.copy()
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if self.rgb else frame

    def close(self):
        for step in (self.tello.streamoff, self.tello.end):
            try:
                step()
            except Exception:
                pass


# -------------------------------------------------------------- elaborazione

class Pipeline:
    """Logica di sorveglianza di una sorgente: ogni sorgente ha i propri
    monitor, così webcam e drone non si confondono a vicenda."""

    def __init__(self, args, name: str):
        self.name = name
        self.args = args
        self.persons = PersonMonitor(
            restricted_area_tolerance_px=args.tolleranza,
            restricted_area_alarm_after_sec=args.attesa,
            fall_alarm_after_sec=args.attesa,
            clear_after_sec=1.0,
        )
        self.dpi = DpiMonitor(
            required_items=[s.strip() for s in args.dpi.split(",")],
            alarm_after_sec=args.attesa,
            clear_after_sec=1.0,
        )
        self.tags = Tags(args.tag_family, args.margine_tag)
        self.net_latch = AlarmLatch(alarm_after_sec=args.rete_attesa, clear_after_sec=1.0)
        self.active = {}  # allarmi accesi: chiave -> testo
        self.fall_until = 0.0

    def process(self, frame, models, params, zone=None):
        raw = detect(models, frame, params["conf"], self.args.imgsz, params["device"])
        if zone:
            raw.append({"name": AREA_MODEL, "detections": [{
                "label": "Red Rectangle", "confidence": 1.0,
                "bbox": tuple(float(v) for v in zone), "manual": True}]})
        self.tags.update(frame)
        corridor = self.tags.corridor if params["corridor"] else None
        filtered, kept = select_people(raw, params["max_people"], params["min_h"],
                                       frame.shape[0], corridor)
        on = params["surveillance"]
        alerts = self.persons.update(detections_by_model=filtered, supervision_active=on)
        alerts += self.dpi.update(detections_by_model=filtered, supervision_active=on)

        # Rete di sicurezza: come in volo, la si cerca solo dove ci sono i tag
        # delle barriere. Tag in vista e nessuna rete per qualche secondo: allarme.
        if NET_MODEL in models and models[NET_MODEL]["enabled"] and on:
            net_seen = any(e["name"] == NET_MODEL and e["detections"] for e in raw)
            was_on = self.net_latch.is_active("rete")
            if self.net_latch.update("rete", bool(self.tags.visible) and not net_seen):
                alerts.append({"type": "safety_net_missing",
                               "message": "Rete di sicurezza assente tra le barriere"})
            if was_on and not self.net_latch.is_active("rete"):
                self.active.pop("rete", None)
                alerts.append({"type": "safety_net_ok", "message": "Rete presente"})

        for a in alerts:
            kind = a["type"]
            if kind == "restricted_area":
                self.active["area"] = "Persona in area vietata"
            elif kind == "restricted_area_ok":
                self.active.pop("area", None)
            elif kind in ("dpi_missing", "dpi_missing_update"):
                self.active["dpi"] = "DPI mancanti: " + ", ".join(a.get("missing", ()))
            elif kind == "dpi_ok":
                self.active.pop("dpi", None)
            elif kind == "safety_net_missing":
                self.active["rete"] = "Rete di sicurezza mancante"
            elif kind == "fall":
                self.fall_until = time.monotonic() + 5.0
        return {"raw": raw, "filtered": filtered, "kept": kept, "corridor": corridor,
                "tags": self.tags.visible, "alerts": alerts}

    def banners(self) -> list[str]:
        out = list(self.active.values())
        if time.monotonic() < self.fall_until:
            out.append("Persona caduta")
        return out

    def stop(self) -> list[dict]:
        """La sorgente non è più mostrata: si spengono i suoi allarmi e si
        restituiscono i messaggi che li chiudono anche sull'orologio."""
        closing = []
        if "area" in self.active:
            closing.append({"type": "restricted_area_ok", "message": "Area vietata libera"})
        if "dpi" in self.active:
            closing.append({"type": "dpi_ok", "message": "DPI di nuovo presenti"})
        self.persons.reset()
        self.dpi.reset()
        self.net_latch.reset()
        self.active.clear()
        self.fall_until = 0.0
        return closing


# ------------------------------------------------------------------ intro

def show_intro() -> None:
    """Schermata di presentazione dell'interfaccia di volo (pygame), la stessa
    che compare all'avvio del drone. Si prosegue con il pulsante Avanti."""
    try:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        from drone.ui.setup.welcome import show_welcome_screen
    except Exception as exc:
        print(f"Schermata iniziale non disponibile ({exc}): si parte direttamente")
        return
    pygame.init()
    try:
        title = getattr(APP_CONFIG, "window_title", PROJECT_TITLE) if overlay else PROJECT_TITLE
        pygame.display.set_caption(title)
        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        if not show_welcome_screen(screen):
            pygame.quit()
            sys.exit("Uscita dalla schermata iniziale")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Schermata iniziale non riuscita ({exc}): si parte direttamente")
    finally:
        try:
            pygame.display.quit()
        except Exception:
            pass


# ------------------------------------------------------------------------ main

def parse_args():
    p = argparse.ArgumentParser(description="Demo webcam di WORK-GUARDIAN")
    p.add_argument("--sorgente", default="auto",
                   help="auto (webcam scelta in automatico), indice webcam o percorso di un video")
    p.add_argument("--drone", action="store_true",
                   help="usa anche la camera del Tello (PC collegato al Wi-Fi del drone)")
    p.add_argument("--no-intro", action="store_true", help="salta la schermata iniziale")
    p.add_argument("--modelli", default="dpi,persone,aree",
                   help="tra dpi, persone, aree, reti (separati da virgola)")
    p.add_argument("--dpi", default="helmet,vest",
                   help="DPI richiesti tra helmet, vest, goggles, shoes")
    p.add_argument("--conf", type=float, default=0.5)
    p.add_argument("--imgsz", type=int, default=640, help="320 o 480 su PC lenti")
    p.add_argument("--persone", type=int, default=2, help="persone considerate")
    p.add_argument("--altezza-min", type=float, default=0.25,
                   help="altezza minima di una persona, in frazione dell'immagine")
    p.add_argument("--attesa", type=float, default=2.0,
                   help="secondi di conferma prima di un allarme")
    p.add_argument("--tolleranza", type=float, default=15.0,
                   help="pixel tra piedi e zona vietata")
    p.add_argument("--tag-family", default="tag25h9", help="famiglia AprilTag del laboratorio")
    p.add_argument("--margine-tag", type=float, default=0.03,
                   help="margine del corridoio oltre i tag, in frazione della larghezza")
    p.add_argument("--rete-attesa", type=float, default=3.0,
                   help="secondi senza rete, con i tag in vista, prima dell'allarme")
    p.add_argument("--mqtt", default=None, help="indirizzo del broker, es. 127.0.0.1")
    p.add_argument("--porta", type=int, default=1883)
    return p.parse_args()


ROLES = {"dpi": DPI_MODEL, "persone": PERSON_MODEL, "aree": AREA_MODEL, "reti": NET_MODEL}
KEYS = {ord("1"): DPI_MODEL, ord("2"): PERSON_MODEL, ord("3"): AREA_MODEL, ord("4"): NET_MODEL}
ROLE_NAMES = {DPI_MODEL: "DPI", PERSON_MODEL: "persone", AREA_MODEL: "aree vietate",
              NET_MODEL: "reti di sicurezza"}
VIEW_NAMES = {"webcam": "Visuale webcam", "drone": "Visuale drone"}


def status_panel_lines(fps, device_name, models, params, pipeline, mqtt, view, show_help):
    """Righe del pannello di stato, nello stesso formato dei log: etichetta,
    simbolo e valore. Con H il pannello elenca i comandi."""
    def row(label, value, glyph=GLYPH_INFO):
        return f"{label:<10}{glyph}  {value}"

    if show_help:
        rows = []
        for line in HELP:
            key, _sep, desc = line.partition("  ")
            rows.append(row(key.strip(), desc.strip(), GLYPH_NET))
        return rows
    on = ", ".join(ROLE_NAMES[n] for n, m in models.items() if m["enabled"]) or "nessuno"
    if mqtt.client is None:
        mq = "spento"
    else:
        mq = "collegato" if mqtt.connected else "non collegato"
        mq += f", missione {'avviata' if mqtt.mission else 'ferma'}"
    if not params["corridor"]:
        tg = "spento"
    else:
        tg = "attivo" if pipeline.tags.corridor else "nessun tag in vista"
    sorv = params["surveillance"]
    return [
        row("Sorgente", VIEW_NAMES[view].replace("Visuale ", "")),
        row("Velocità", f"{fps:.1f} FPS su {device_name}".replace(".", ","),
            GLYPH_INFO if fps >= 10 else GLYPH_WARN),
        row("Modelli", on),
        row("Sorveglia", "attiva" if sorv else "spenta", GLYPH_INFO if sorv else GLYPH_WARN),
        row("Soglia", f"{params['conf']:.2f}".replace(".", ",")),
        row("Persone", f"{params['max_people']}, altezza min. {params['min_h']:.2f}".replace(".", ",")),
        row("Corridoio", tg),
        row("Orologio", mq, GLYPH_INFO if (mqtt.client is None or mqtt.connected) else GLYPH_WARN),
        row("Comandi", "tasto H"),
    ]


def main():
    args = parse_args()
    if not args.no_intro:
        show_intro()

    # Colonna di destra; da qui in poi i messaggi del programma compaiono
    # anche nel pannello "Terminale demo", come nell'interfaccia di volo.
    side = SidePanel()
    original_stdout = sys.stdout
    if StdoutRedirect is not None:
        sys.stdout = StdoutRedirect(original_stdout, side.log_terminal, echo_to_original=True)

    wanted = [ROLES[r.strip()] for r in args.modelli.split(",") if r.strip() in ROLES]
    models = load_models(wanted)
    device, device_name = inference_device()

    sources = {"webcam": WebcamSource(args.sorgente)}
    if args.drone:
        try:
            sources["drone"] = DroneSource()
        except Exception as exc:
            print(f"Drone non raggiungibile ({exc}): si continua solo con la webcam. "
                  "Il PC deve essere collegato al Wi-Fi del Tello.")
    views = ["webcam"] + (["drone"] if "drone" in sources else [])
    view = "drone" if "drone" in sources else "webcam"
    pipelines = {name: Pipeline(args, name) for name in sources}
    mqtt = Mqtt(args.mqtt, args.porta)
    mqtt.on_alert = side.log_alert

    # Zona vietata disegnata col mouse: si salva per la sorgente su cui è
    # stata disegnata, in coordinate dell'immagine analizzata.
    state = {"zones": {}, "drag": None, "layout": [], "offset": (0, 0)}

    def where(x, y):
        x, y = x - state["offset"][0], y - state["offset"][1]
        for name, (x0, y0, w, h, scale) in state["layout"]:
            if x0 <= x < x0 + w and y0 <= y < y0 + h:
                return name, (x - x0) / scale, (y - y0) / scale
        return None, 0, 0

    def on_mouse(event, x, y, _flags, _param):
        name, fx, fy = where(x, y)
        if event == cv2.EVENT_LBUTTONDOWN and name:
            state["drag"] = (name, fx, fy)
        elif event == cv2.EVENT_MOUSEMOVE and state["drag"] and name == state["drag"][0]:
            _n, x0, y0 = state["drag"]
            state["zones"][name] = (min(x0, fx), min(y0, fy), max(x0, fx), max(y0, fy))
        elif event == cv2.EVENT_LBUTTONUP:
            state["drag"] = None
        elif event == cv2.EVENT_RBUTTONDOWN and name:
            state["zones"].pop(name, None)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    window_fitted = None
    cv2.setMouseCallback(WINDOW, on_mouse)

    params = {"conf": args.conf, "max_people": args.persone, "min_h": args.altezza_min,
              "surveillance": True, "corridor": True, "device": device}
    show_help, fullscreen = False, False
    toast = None  # (tasto, messaggio, fino a quando mostrarlo)
    t_prev = time.monotonic()
    fps = 0.0
    last = {}  # ultima immagine valida di ogni sorgente

    while True:
        shown = ["webcam", "drone"] if view == "entrambe" else [view]
        results = {}
        for name in shown:
            frame = sources[name].read()
            if frame is None:
                if sources[name].failed > 150 and name == "webcam" and len(sources) == 1:
                    print("La webcam ha smesso di mandare immagini.")
                    shown = []
                    break
                frame = last.get(name)
                if frame is None:
                    continue
                results[name] = (frame, None)  # nessuna immagine nuova: niente analisi
                continue
            last[name] = frame
            results[name] = (frame, pipelines[name].process(
                frame, models, params, state["zones"].get(name)))
        if not shown:
            break
        if not results:
            time.sleep(0.02)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
            continue

        # MQTT: l'orologio riceve gli allarmi di una sola sorgente, quella
        # principale (la prima mostrata), per non ricevere segnali opposti.
        main_src = shown[0]
        for name, (_f, res) in results.items():
            if res is None:
                continue
            for a in res["alerts"]:
                kind = a["type"]
                # Come in drone/flight/steps.py: nel log vanno i pericoli,
                # non i messaggi che spengono o aggiornano un allarme.
                if kind == "safety_net_missing":
                    side.log_alert(GLYPH_ALERT, "Rete mancante tra le barriere")
                elif kind == "safety_net_ok":
                    side.log_alert(GLYPH_NET, "Rete presente tra le barriere")
                elif kind not in ("dpi_ok", "dpi_missing_update", "restricted_area_ok"):
                    side.log_alert(GLYPH_WARN, a.get("message") or a.get("title") or kind)
                if name == main_src:
                    mqtt.alarm(a)

        # Composizione come nell'interfaccia di volo: video a sinistra,
        # colonna dei pannelli a destra.
        name = shown[0]
        frame, res = results[name]
        scale = VIDEO_HEIGHT / frame.shape[0]
        video = cv2.resize(frame, (int(round(frame.shape[1] * scale)), VIDEO_HEIGHT))
        if res is not None:
            draw_view(video, scale, res, models, state["zones"].get(name))
        state["layout"] = [(name, (0, 0, video.shape[1], video.shape[0], scale))]
        draw_hud(video, mqtt.watch, mqtt.client is not None,
                 net_missing="rete" in pipelines[name].active)
        if toast and time.monotonic() < toast[2]:
            draw_toast(video, toast[0], toast[1])

        now = time.monotonic()
        fps = 0.9 * fps + 0.1 / max(1e-3, now - t_prev)
        t_prev = now
        status_lines = status_panel_lines(
            fps, device_name, models, params, pipelines[name], mqtt, view, show_help)
        column = side.render(VIDEO_HEIGHT, status_lines,
                             "Comandi demo" if show_help else "Stato demo")
        gap = np.full((VIDEO_HEIGHT, PANEL_GAP, 3), side._bg(), dtype=np.uint8)
        display = np.hstack((video, gap, column))
        if window_fitted != display.shape[:2]:  # prima immagine o cambio sorgente
            fit_window(WINDOW, display.shape[1], display.shape[0])
            window_fitted = display.shape[:2]
            cv2.waitKey(1)
        boxed, state["offset"] = letterbox(display, WINDOW)
        cv2.imshow(WINDOW, boxed)

        key = cv2.waitKey(1) & 0xFF
        msg = None
        if key in (ord("q"), 27):
            break
        elif key == ord("h"):
            show_help = not show_help
        elif key == ord("s"):
            params["surveillance"] = not params["surveillance"]
            msg = f"Sorveglianza {'accesa' if params['surveillance'] else 'spenta'}"
        elif key in (ord("+"), ord("=")):
            params["conf"] = min(0.95, params["conf"] + 0.05)
            msg = f"Soglia di confidenza {params['conf']:.2f}"
        elif key == ord("-"):
            params["conf"] = max(0.05, params["conf"] - 0.05)
            msg = f"Soglia di confidenza {params['conf']:.2f}"
        elif key == ord("p"):
            params["max_people"] = params["max_people"] % 3 + 1
            msg = f"Persone considerate: {params['max_people']}"
        elif key == ord("a"):
            params["min_h"] = round(params["min_h"] + 0.05, 2) if params["min_h"] < 0.6 else 0.0
            msg = f"Altezza minima persona: {params['min_h']:.2f}"
        elif key == ord("t"):
            params["corridor"] = not params["corridor"]
            msg = f"Corridoio tra le AprilTag {'acceso' if params['corridor'] else 'spento'}"
        elif key == ord("r"):
            for pl in pipelines.values():
                pl.tags.reset()
            msg = "Corridoio azzerato: lo ricalcolo dai tag"
        elif key == ord("m"):
            mqtt.set_mission(not mqtt.mission)
            msg = f"Missione orologio {'avviata' if mqtt.mission else 'terminata'}"
        elif key == ord("w"):
            if len(views) > 1:
                old = set(shown)
                view = views[(views.index(view) + 1) % len(views)]
                new = set(["webcam", "drone"] if view == "entrambe" else [view])
                for name in old - new:  # sorgente nascosta: si chiudono i suoi allarmi
                    for a in pipelines[name].stop():
                        if name == main_src:
                            mqtt.alarm(a)
                msg = VIEW_NAMES[view]
            else:
                msg = "Drone non collegato: avviare con --drone"
        elif key == ord("c"):
            if sources["webcam"].next_camera():
                msg = f"Webcam {sources['webcam'].index}"
            else:
                msg = "Nessun'altra webcam disponibile"
        elif key == ord("f"):
            fullscreen = not fullscreen
            cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
        elif key in KEYS:
            name = KEYS[key]
            if name not in models:
                # Modello non caricato all'avvio: lo si carica adesso (qualche
                # secondo, l'immagine si ferma un istante).
                loading = display.copy()
                draw_toast(loading, chr(key), f"Caricamento modello {ROLE_NAMES[name]}...")
                cv2.imshow(WINDOW, letterbox(loading, WINDOW)[0])
                cv2.waitKey(1)
                m = load_model(name)
                if m is not None:
                    models[name] = m
            else:
                models[name]["enabled"] = not models[name]["enabled"]
            if name in models:
                msg = f"Modello {ROLE_NAMES[name]} {'acceso' if models[name]['enabled'] else 'spento'}"
        if msg:
            # Conferma a schermo: si vede subito se il tasto è arrivato.
            toast = (chr(key).upper(), msg, time.monotonic() + 2.0)

    for src in sources.values():
        src.close()
    cv2.destroyAllWindows()
    mqtt.close()
    sys.stdout = original_stdout


if __name__ == "__main__":
    main()
