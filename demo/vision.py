"""Riconoscimento della demo.

I modelli YOLO sono quelli di vision/models (catalog.json) e gli allarmi li
decide la stessa logica del volo (drone/surveillance). Tutto gira in un thread
separato dalla finestra: il video resta fluido anche quando il riconoscimento
è più lento, e sopra il video si disegna l'ultimo risultato disponibile, come
fa l'interfaccia di volo.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from drone.surveillance.alarm_latch import AlarmLatch
from drone.surveillance.dpi_monitor import DpiMonitor
from drone.surveillance.person_monitor import PersonMonitor

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "vision" / "models"


def _model_roles() -> tuple[dict, str, str]:
    """Nomi dei modelli per ruolo, come li usa il volo: da APP_CONFIG, che li
    legge dai "roles" di catalog.json. Così la demo segue eventuali
    rinomine dei modelli fatte dal gruppo."""
    try:
        from drone.config import APP_CONFIG as C
        return ({"dpi": C.dpi_model_name, "persone": C.person_fall_model_name,
                 "aree": C.restricted_area_model_name, "reti": C.safety_net_model_name},
                C.person_class_label, getattr(C, "fall_class_label", "Fall"))
    except Exception:
        roles = json.loads((MODELS_DIR / "catalog.json").read_text(encoding="utf-8"))["roles"]
        return ({"dpi": roles["dpi"], "persone": roles["person_fall"],
                 "aree": roles["restricted_area"], "reti": roles["safety_net"]}, "Person", "Fall")


ROLES, PERSON_LABEL, FALL_LABEL = _model_roles()
DPI_MODEL, PERSON_MODEL = ROLES["dpi"], ROLES["persone"]
AREA_MODEL, NET_MODEL = ROLES["aree"], ROLES["reti"]
ROLE_NAMES = {DPI_MODEL: "DPI", PERSON_MODEL: "persone", AREA_MODEL: "aree vietate",
              NET_MODEL: "reti di sicurezza"}
PERSON_LABELS = {PERSON_LABEL.casefold(), FALL_LABEL.casefold()}

# Modelli di oggetti fermi (area vietata, rete) e ogni quanto rieseguirli;
# ogni quanto cercare le AprilTag, che stanno sulle barriere ferme.
STATIC_MODELS = {AREA_MODEL, NET_MODEL}
STATIC_REFRESH = 0.3
TAG_REFRESH = 0.2


# -------------------------------------------------------------------- modelli

def pick_device() -> tuple[object, str]:
    """GPU NVIDIA se PyTorch la vede, altrimenti CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return 0, f"GPU {torch.cuda.get_device_name(0)}"
    except Exception:
        pass
    return "cpu", "CPU"


class ModelBank:
    """Modelli del catalogo, caricati all'avvio o al momento (tasti 1-4)."""

    def __init__(self, names: list[str], imgsz: int = 640) -> None:
        catalog = json.loads((MODELS_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.catalog = {Path(e["weights"]).parent.parent.name: e for e in catalog["models"]}
        self.imgsz = imgsz
        self.device, self.device_name = pick_device()
        self.half = self.device != "cpu"
        self.models: dict[str, dict] = {}
        self.loading: set[str] = set()
        # Ultimi riconoscimenti dei modelli di oggetti fermi, per sorgente.
        self._static: dict[tuple[str, str], tuple[float, list, float]] = {}
        self.messages: "queue.SimpleQueue[str]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        print(f"Riconoscimento su {self.device_name}")
        if self.device == "cpu":
            print("Senza GPU la demo è più lenta: con una scheda NVIDIA va installato "
                  "PyTorch con CUDA (vedi demo/info.txt).")
        for name in names:
            self._load(name)

    def _load(self, name: str) -> bool:
        entry = self.catalog.get(name)
        if entry is None:
            print(f"Modello {name} non trovato in vision/models/catalog.json")
            return False
        from ultralytics import YOLO
        print(f"Caricamento di {name}...")
        model = YOLO(str(MODELS_DIR / entry["weights"]))
        try:  # la prima inferenza prepara la GPU: meglio farla adesso
            model.predict(np.zeros((self.imgsz, self.imgsz, 3), np.uint8), imgsz=self.imgsz,
                          device=self.device, half=self.half, verbose=False)
        except Exception:
            pass
        with self._lock:
            self.models[name] = {
                "yolo": model,
                "color": tuple(int(c) for c in entry.get("color", (0, 255, 0))),
                "classes": entry.get("classes", {}),
                "enabled": True,
            }
        return True

    def toggle(self, name: str) -> str:
        """Accende o spegne un modello; se non è caricato lo carica in
        background e il messaggio arriva poi su self.messages."""
        with self._lock:
            model = self.models.get(name)
            if model is not None:
                model["enabled"] = not model["enabled"]
                return f"Modello {ROLE_NAMES[name]} {'acceso' if model['enabled'] else 'spento'}"
            if name in self.loading:
                return f"Modello {ROLE_NAMES[name]} in caricamento"
            self.loading.add(name)

        def load() -> None:
            ok = self._load(name)
            with self._lock:
                self.loading.discard(name)
            self.messages.put(f"Modello {ROLE_NAMES[name]} {'acceso' if ok else 'non trovato'}")

        threading.Thread(target=load, daemon=True).start()
        return f"Caricamento modello {ROLE_NAMES[name]}..."

    def enabled(self) -> list[tuple[str, dict]]:
        with self._lock:
            return [(n, m) for n, m in self.models.items() if m["enabled"]]

    def detect(self, frame: np.ndarray, conf: float, source: str = "") -> list[dict]:
        """Riconoscimenti nello stesso formato di drone/perception/object_detector.py.

        Area vietata e rete sono oggetti fermi: i loro modelli si rieseguono al
        massimo ogni STATIC_REFRESH secondi e nel frattempo vale l'ultimo
        risultato. DPI e persone si muovono e girano su ogni immagine."""
        out = []
        now = time.monotonic()
        for name, m in self.enabled():
            key = (source, name)
            if name in STATIC_MODELS:
                cached = self._static.get(key)
                if cached is not None and now - cached[0] < STATIC_REFRESH and cached[2] == conf:
                    out.append({"name": name, "detections": cached[1]})
                    continue
            result = m["yolo"].predict(frame, conf=conf, imgsz=self.imgsz, device=self.device,
                                       half=self.half, verbose=False)[0]
            dets = []
            if result.boxes is not None and len(result.boxes):
                # Un solo passaggio dalla GPU per tutti i riquadri, invece di
                # leggere i valori un riquadro alla volta.
                boxes = result.boxes.cpu().numpy()
                names = result.names
                dets = [{"label": names[int(c)], "confidence": float(p),
                         "bbox": (float(b[0]), float(b[1]), float(b[2]), float(b[3]))}
                        for b, p, c in zip(boxes.xyxy, boxes.conf, boxes.cls)]
            if name in STATIC_MODELS:
                self._static[key] = (now, dets, conf)
            out.append({"name": name, "detections": dets})
        return out

    def forget(self, source: str) -> None:
        """Dimentica i riconoscimenti fermi di una sorgente non più mostrata."""
        for key in [k for k in self._static if k[0] == source]:
            del self._static[key]


# ------------------------------------------------------ filtro sulle persone

def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inside(b, person, margin: float = 0.10) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    w, h = person[2] - person[0], person[3] - person[1]
    return (person[0] - margin * w <= cx <= person[2] + margin * w
            and person[1] - margin * h <= cy <= person[3] + margin * h)


def select_people(dets: list[dict], max_people: int, min_height: float, frame_h: int,
                  corridor: Optional[tuple[float, float]] = None):
    """Differenza rispetto al volo: in uno stand ci sono molte persone, quindi
    si considerano solo le più vicine (i riquadri più grandi), solo quelle
    abbastanza alte nell'immagine e, se ci sono le AprilTag, solo quelle con i
    piedi tra le barriere. I DPI contano solo se cadono dentro di loro. Aree
    interdette e reti passano sempre; senza il modello delle persone non si
    filtra nulla."""
    if not any(e["name"] == PERSON_MODEL for e in dets):
        return dets, []
    people = [
        d["bbox"] for e in dets if e["name"] == PERSON_MODEL
        for d in e["detections"] if d["label"].casefold() in PERSON_LABELS
        and (d["bbox"][3] - d["bbox"][1]) >= min_height * frame_h
        and (corridor is None or corridor[0] <= (d["bbox"][0] + d["bbox"][2]) / 2 <= corridor[1])
    ]
    kept = sorted(people, key=_area, reverse=True)[:max_people]
    filtered = []
    for e in dets:
        if e["name"] in (AREA_MODEL, NET_MODEL):
            filtered.append(e)
        else:
            filtered.append({"name": e["name"], "detections": [
                d for d in e["detections"] if any(_inside(d["bbox"], p) for p in kept)]})
    return filtered, kept


class Tags:
    """AprilTag sulle barriere verticali, come in laboratorio. Il corridoio tra
    il tag più a sinistra e quello più a destra delimita la scena e resta
    memorizzato anche quando una persona copre i tag (tasto R per ricalcolarlo)."""

    def __init__(self, family: str, margin: float) -> None:
        self.margin = margin
        self.visible: list[tuple[int, list]] = []
        self.corridor: Optional[tuple[float, float]] = None
        self.detector = None
        self._last = 0.0
        try:
            from pyapriltags import Detector
            self.detector = Detector(families=family, quad_decimate=2.0,
                                     nthreads=max(1, min(4, (os.cpu_count() or 2) // 2)))
        except Exception as exc:
            print(f"AprilTag non disponibili ({exc}): corridoio disattivato")

    def update(self, frame: np.ndarray) -> None:
        """Le barriere non si muovono: basta cercare i tag qualche volta al
        secondo, non su ogni immagine (su un'immagine piena di dettagli la
        ricerca costa quanto un modello)."""
        now = time.monotonic()
        if self.detector is None or now - self._last < TAG_REFRESH:
            return
        self._last = now
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.visible = [(int(d.tag_id), d.corners.tolist()) for d in self.detector.detect(gray)]
        if len(self.visible) >= 2:
            xs = [c[0] for _id, corners in self.visible for c in corners]
            pad = self.margin * frame.shape[1]
            self.corridor = (max(0.0, min(xs) - pad), min(float(frame.shape[1]), max(xs) + pad))

    def reset(self) -> None:
        self.corridor = None
        self._last = 0.0


# ---------------------------------------------------------------- sorveglianza

# Allarmi che si accendono (True) o si spengono (False), per tipo di messaggio.
_ACTIVE_CHANGES = {"restricted_area": ("area", True), "restricted_area_ok": ("area", False),
                   "dpi_missing": ("dpi", True), "dpi_ok": ("dpi", False),
                   "safety_net_missing": ("rete", True), "safety_net_ok": ("rete", False)}


class Surveillance:
    """Monitor del volo applicati a una sorgente. Ogni sorgente ha i propri,
    così webcam e drone non si confondono a vicenda."""

    def __init__(self, args) -> None:
        self.persons = PersonMonitor(
            fall_model_name=PERSON_MODEL,
            person_model_name=PERSON_MODEL,
            restricted_area_model_name=AREA_MODEL,
            person_label=PERSON_LABEL,
            fall_label=FALL_LABEL,
            restricted_area_tolerance_px=args.tolleranza,
            restricted_area_alarm_after_sec=args.attesa,
            fall_alarm_after_sec=args.attesa,
            clear_after_sec=1.0,
        )
        self.dpi = DpiMonitor(
            dpi_model_name=DPI_MODEL,
            required_items=[s.strip() for s in args.dpi.split(",") if s.strip()],
            alarm_after_sec=args.attesa,
            clear_after_sec=1.0,
        )
        self.tags = Tags(args.tag_family, args.margine_tag)
        self.net = AlarmLatch(alarm_after_sec=args.rete_attesa, clear_after_sec=1.0)
        self.active: set[str] = set()  # allarmi accesi: "area", "dpi", "rete"

    def process(self, frame, dets, params, zone=None, net_model_on=False) -> dict:
        if zone:
            dets = dets + [{"name": AREA_MODEL, "detections": [{
                "label": "Red Rectangle", "confidence": 1.0, "bbox": tuple(zone), "manual": True}]}]
        # I tag servono al corridoio e alla rete: con entrambi spenti non si cercano.
        if params["corridor"] or net_model_on:
            self.tags.update(frame)
        else:
            self.tags.visible = []
        corridor = self.tags.corridor if params["corridor"] else None
        filtered, _kept = select_people(dets, params["max_people"], params["min_h"],
                                       frame.shape[0], corridor)
        on = params["surveillance"]
        alerts = self.persons.update(detections_by_model=filtered, supervision_active=on)
        alerts += self.dpi.update(detections_by_model=filtered, supervision_active=on)

        # Rete di sicurezza: come in volo la si cerca solo dove ci sono i tag
        # delle barriere. Tag in vista e nessuna rete per qualche secondo: allarme.
        if net_model_on and on:
            seen = any(e["name"] == NET_MODEL and e["detections"] for e in dets)
            was_on = self.net.is_active("rete")
            if self.net.update("rete", bool(self.tags.visible) and not seen):
                alerts.append({"type": "safety_net_missing", "message": "Rete mancante tra le barriere"})
            elif was_on and not self.net.is_active("rete"):
                alerts.append({"type": "safety_net_ok", "message": "Rete presente tra le barriere"})

        for a in alerts:
            change = _ACTIVE_CHANGES.get(a["type"])
            if change:
                (self.active.add if change[1] else self.active.discard)(change[0])
        # Riconoscimenti esclusi dal filtro sulle persone: si disegnano in grigio.
        counted = {id(d) for e in filtered for d in e["detections"]}
        ignored = {id(d) for e in dets if e["name"] in (DPI_MODEL, PERSON_MODEL)
                   for d in e["detections"] if id(d) not in counted}
        return {"detections": dets, "ignored": ignored, "corridor": corridor,
                "tags": self.tags.visible, "alerts": alerts, "net_missing": "rete" in self.active}

    def reset(self) -> list[dict]:
        """La sorgente non è più mostrata: si spengono i suoi allarmi e si
        restituiscono i messaggi che li chiudono anche sull'orologio."""
        closing = []
        if "area" in self.active:
            closing.append({"type": "restricted_area_ok", "message": "Area vietata libera"})
        if "dpi" in self.active:
            closing.append({"type": "dpi_ok", "message": "DPI di nuovo presenti"})
        self.persons.reset()
        self.dpi.reset()
        self.net.reset()
        self.active.clear()
        return closing


class VisionWorker(threading.Thread):
    """Thread del riconoscimento: analizza l'ultima immagine della sorgente
    attiva, appena ne arriva una nuova, e mette gli allarmi in una coda."""

    def __init__(self, bank: ModelBank, sources: dict, params: dict, zones: dict, args) -> None:
        super().__init__(name="riconoscimento", daemon=True)
        self.bank = bank
        self.sources = sources
        self.params = params
        self.zones = zones
        self.surveillance = {name: Surveillance(args) for name in sources}
        self.active = next(iter(sources))
        self.alerts: "queue.SimpleQueue[tuple[str, dict]]" = queue.SimpleQueue()
        self.fps = 0.0
        self._result: Optional[dict] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._error_shown = False

    def result(self, source: str) -> Optional[dict]:
        with self._lock:
            res = self._result
        return res if res is not None and res["source"] == source else None

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2.0)

    def run(self) -> None:
        current = self.active
        last_seq: dict[str, int] = {}
        last_t = 0.0
        while not self._stop.is_set():
            name = self.active
            if name != current:
                for a in self.surveillance[current].reset():
                    self.alerts.put((current, a))
                self.bank.forget(current)
                with self._lock:
                    self._result = None
                current = name
            frame, seq = self.sources[name].latest()
            if frame is None or seq == last_seq.get(name):
                time.sleep(0.003)
                continue
            last_seq[name] = seq
            try:
                dets = self.bank.detect(frame, self.params["conf"], name)
                net_on = any(n == NET_MODEL for n, _m in self.bank.enabled())
                res = self.surveillance[name].process(
                    frame, dets, self.params, self.zones.get(name), net_on)
            except Exception as exc:
                if not self._error_shown:
                    self._error_shown = True
                    print(f"Errore nel riconoscimento: {exc}")
                time.sleep(0.1)
                continue
            res.update(source=name, seq=seq, shape=frame.shape[:2])
            for a in res["alerts"]:
                self.alerts.put((name, a))
            with self._lock:
                self._result = res
            now = time.monotonic()
            if last_t:
                self.fps = 0.85 * self.fps + 0.15 / max(1e-3, now - last_t)
            last_t = now
