"""Sorgenti video della demo: webcam (o file video) e camera del Tello.

Ogni sorgente legge le immagini in un thread proprio e conserva solo l'ultima.
Il riconoscimento e la finestra la prendono quando servono, senza restare in
attesa della camera e senza accumulare ritardo.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

# Nasconde i messaggi interni di OpenCV mentre si cercano le webcam.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

# Sotto questa luminosità media l'immagine è nera; sotto questa variazione tra
# due fotogrammi l'immagine è ferma (fotocamera virtuale con un logo fisso).
_BLACK_LEVEL = 8.0
_STILL_LEVEL = 0.05


class Source:
    """Base comune: un thread scrive l'ultima immagine, latest() la legge."""

    name = "sorgente"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status = "avvio"
        self.fps = 0.0
        self._last_t = 0.0

    def _start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def _run(self) -> None:  # pragma: no cover - definita nelle sottoclassi
        raise NotImplementedError

    def _publish(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if self._last_t:
            self.fps = 0.9 * self.fps + 0.1 / max(1e-3, now - self._last_t)
        self._last_t = now
        with self._lock:
            self._frame = frame
            self._seq += 1

    def latest(self) -> tuple[Optional[np.ndarray], int]:
        """Ultima immagine e il suo numero progressivo (cambia a ogni nuova)."""
        with self._lock:
            return self._frame, self._seq

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


# --------------------------------------------------------------------- webcam

def _backends() -> list[int]:
    # Su Windows il driver predefinito (MSMF) con alcune webcam si apre ma non
    # manda immagini: si prova prima DirectShow.
    if sys.platform == "win32":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    return [cv2.CAP_ANY]


def _try_open(index: int, backend: int, fmt: Optional[str], reads: int = 15):
    """Apre una webcam e restituisce (cap, luminosità, variazione) oppure
    (None, 0, 0) se non si apre."""
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None, 0.0, 0.0
    if fmt:
        # Con DirectShow molte webcam mandano immagini nere finché non si
        # impostano formato e risoluzione.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    brightness, change, prev = 0.0, 0.0, None
    for _ in range(reads):  # i primi fotogrammi possono essere scuri
        ok, frame = cap.read()
        if ok and frame is not None:
            brightness = max(brightness, float(frame.mean()))
            small = cv2.resize(frame, (160, 90)).astype(np.float32)
            if prev is not None:
                change = max(change, float(np.abs(small - prev).mean()))
            prev = small
        time.sleep(0.03)
    return cap, brightness, change


def scan_webcams(indices=range(5)) -> list[dict]:
    """Controlla le webcam indicate e restituisce quelle utilizzabili: prima
    quelle vere, dalla più luminosa, poi quelle con un'immagine ferma
    (fotocamere virtuali). Quelle che mandano solo nero sono scartate."""
    print("Ricerca delle webcam...")
    found = []
    for index in indices:
        best = None
        for backend in _backends():
            for fmt in ("MJPG", None):
                cap, brightness, change = _try_open(index, backend, fmt)
                if cap is None:
                    continue
                name = cap.getBackendName()
                cap.release()
                real = brightness > _BLACK_LEVEL and change > _STILL_LEVEL
                score = (real, brightness)
                if best is None or score > best["score"]:
                    best = {"indice": index, "backend": backend, "driver": name,
                            "formato": fmt, "luminosita": brightness,
                            "vera": real, "score": score}
                if real and brightness > 20:
                    break
            if best and best["vera"] and best["luminosita"] > 20:
                break
        if best is None:
            continue
        if best["luminosita"] <= _BLACK_LEVEL:
            esito = "immagine nera, scartata"
        elif not best["vera"]:
            esito = "immagine ferma, probabilmente una fotocamera virtuale"
        else:
            esito = "ok"
        print(f"  webcam {index}: luminosità {best['luminosita']:.0f} ({best['driver']}) - {esito}")
        if best["luminosita"] > _BLACK_LEVEL:
            found.append(best)
    found.sort(key=lambda c: c["score"], reverse=True)
    return found


class WebcamSource(Source):
    """Webcam scelta in automatico (o indicata) oppure file video in loop."""

    name = "webcam"

    def __init__(self, spec: str = "auto") -> None:
        super().__init__()
        self.cams: list[dict] = []
        self.pos = 0
        self.is_file = not (spec == "auto" or spec.isdigit())
        self._switch = False
        if self.is_file:
            self.cap = cv2.VideoCapture(spec)
            if not self.cap.isOpened():
                raise RuntimeError(f"Impossibile aprire il video {spec}")
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
            self._delay = 1.0 / max(1.0, min(60.0, fps))
            self.description = os.path.basename(spec)
        else:
            self.cams = scan_webcams(range(5) if spec == "auto" else [int(spec)])
            if not self.cams:
                raise RuntimeError(
                    "Nessuna webcam manda immagini. Controllare che non sia usata da "
                    "un'altra app (Teams, browser, OBS), che in Impostazioni > Privacy > "
                    "Fotocamera sia consentito l'accesso alle app desktop e che la "
                    "webcam non sia coperta.")
            if not self.cams[0]["vera"]:
                print("ATTENZIONE: nessuna webcam vera trovata, si usa un'immagine ferma. "
                      "Se una webcam è collegata, chiudere le altre app che la usano.")
            self.cap = self._open(self.cams[0])
        self.status = "attiva"
        self._start()

    def _open(self, cam: dict):
        cap, _b, _c = _try_open(cam["indice"], cam["backend"], cam["formato"], reads=3)
        if cap is None:
            raise RuntimeError(f"La webcam {cam['indice']} non si apre più")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.description = f"webcam {cam['indice']} ({cam['driver']}, {w}x{h})"
        print(f"Uso la {self.description}")
        return cap

    def next_camera(self) -> bool:
        """Passa alla webcam successiva tra quelle trovate (tasto C)."""
        if len(self.cams) <= 1:
            return False
        self._switch = True
        return True

    def _run(self) -> None:
        fails = 0
        while not self._stop.is_set():
            if self._switch:
                self._switch = False
                self.cap.release()
                self.pos = (self.pos + 1) % len(self.cams)
                try:
                    self.cap = self._open(self.cams[self.pos])
                except RuntimeError as exc:
                    print(exc)
            ok, frame = self.cap.read()
            if ok and frame is not None:
                fails = 0
                self.status = "attiva"
                self._publish(frame)
                if self.is_file:
                    time.sleep(self._delay)
                continue
            if self.is_file:  # video finito: riparte da capo
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            fails += 1
            if fails > 60:
                self.status = "nessuna immagine dalla webcam"
            time.sleep(0.03)
        self.cap.release()


# ---------------------------------------------------------------------- drone

class DroneSource(Source):
    """Camera del Tello letta con djitellopy, come nel sistema di volo.

    Il collegamento avviene in background e si ritenta finché il drone non
    risponde, così la demo può partire anche con il drone ancora spento. Il
    drone non decolla: riceve solo una richiesta della batteria ogni pochi
    secondi, che lo tiene in contatto con il PC."""

    name = "drone"

    def __init__(self, host: Optional[str] = None, frame_is_rgb: bool = True) -> None:
        super().__init__()
        self.host = host
        self.frame_is_rgb = frame_is_rgb
        self.battery: Optional[int] = None
        self.description = f"drone {host or '(indirizzo predefinito)'}"
        self.status = "collegamento in corso"
        self._tello = None
        self._start()

    def _run(self) -> None:
        try:
            from djitellopy import Tello
        except ImportError:
            self.status = "djitellopy non installato"
            return
        try:
            self._tello = Tello(host=self.host) if self.host else Tello()
        except Exception as exc:  # porta UDP occupata o rete assente
            self.status = f"non disponibile ({exc})"
            return
        tello = self._tello
        while not self._stop.is_set():
            try:
                tello.connect()
                try:
                    tello.streamoff()
                except Exception:
                    pass
                tello.streamon()
                reader = tello.get_frame_read()
            except Exception:
                self.status = "non raggiungibile, nuovo tentativo"
                self._stop.wait(3.0)
                continue
            self.status = "collegato"
            print(f"Drone collegato ({self.host or 'indirizzo predefinito'})")
            self._stream(tello, reader)

    def _stream(self, tello, reader) -> None:
        # L'immagine nera che djitellopy espone prima della prima decodifica
        # conta come già vista; djitellopy crea un oggetto nuovo per ogni
        # immagine ricevuta, quindi lo stesso oggetto vuol dire nessuna novità.
        last = getattr(reader, "frame", None)
        next_ping, failures = 0.0, 0
        while not self._stop.is_set():
            frame = getattr(reader, "frame", None)
            if frame is not None and frame is not last and getattr(frame, "ndim", 0) == 3:
                last = frame
                self._publish(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                              if self.frame_is_rgb else frame.copy())
            else:
                time.sleep(0.005)
            now = time.monotonic()
            if now >= next_ping:
                next_ping = now + 5.0
                try:
                    self.battery = int(tello.query_battery())
                    failures = 0
                    self.status = "collegato"
                except Exception:
                    failures += 1
                    if failures >= 3:
                        self.status = "collegamento perso, nuovo tentativo"
                        return

    def close(self) -> None:
        super().close()
        if self._tello is not None:
            for step in (self._tello.streamoff, self._tello.end):
                try:
                    step()
                except Exception:
                    pass
