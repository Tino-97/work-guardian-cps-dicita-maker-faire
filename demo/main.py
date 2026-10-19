"""Demo da banco di WORK-GUARDIAN per la Maker Faire.

Mostra il riconoscimento del sistema senza far volare il drone: i modelli di
vision/models analizzano il video della webcam (o della camera del Tello, a
terra) e la logica di sorveglianza del volo (drone/surveillance) decide gli
allarmi, che finiscono nel log degli alert e, via MQTT, sull'orologio.

Avvio dalla radice del progetto:

    python demo/main.py                 webcam, broker e orologio automatici
    python demo/main.py --drone         anche la camera del Tello (tasto W)

Tasto H in esecuzione: stato della demo e poi elenco dei comandi.
Dettagli in demo/info.txt.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import cv2  # noqa: E402

from capture import DroneSource, WebcamSource  # noqa: E402
from network import Broker, WatchLink  # noqa: E402
from screen import Screen, ViewState, enable_dpi_awareness  # noqa: E402
from vision import ROLE_NAMES, ROLES, ModelBank, VisionWorker  # noqa: E402

WINDOW = "WORK GUARDIAN - Demo"

COMMANDS = [
    ("H", "stato della demo, comandi, nessuno"),
    ("S", "sorveglianza accesa o spenta"),
    ("1-4", "modelli DPI, persone, aree, reti"),
    ("+  -", "soglia di confidenza"),
    ("P", "persone considerate (1-3)"),
    ("A", "altezza minima di una persona"),
    ("T  R", "corridoio AprilTag; ricalcolo"),
    ("Mouse", "sinistro: zona vietata; destro: cancella"),
    ("W", "visuale webcam o drone"),
    ("C", "altra webcam"),
    ("M", "missione dell'orologio avviata o ferma"),
    ("F", "schermo intero"),
    ("Q  Esc", "esci"),
]
KEY_MODELS = {ord("1"): ROLES["dpi"], ord("2"): ROLES["persone"],
              ord("3"): ROLES["aree"], ord("4"): ROLES["reti"]}


def parse_args():
    p = argparse.ArgumentParser(description="Demo da banco di WORK-GUARDIAN")
    p.add_argument("--sorgente", default="auto",
                   help="auto (webcam scelta in automatico), indice della webcam o file video")
    p.add_argument("--drone", nargs="?", const="auto", default=None, metavar="IP",
                   help="usa anche la camera del Tello; IP solo se il drone è collegato al router")
    p.add_argument("--modelli", default="dpi,persone,aree",
                   help="modelli caricati all'avvio, tra dpi, persone, aree, reti")
    # Come lo scenario "Verifica della sicurezza operatore" del volo.
    p.add_argument("--dpi", default="helmet,goggles,vest,shoes",
                   help="DPI richiesti, tra helmet, goggles, vest, shoes")
    p.add_argument("--conf", type=float, default=0.5, help="soglia di confidenza")
    p.add_argument("--imgsz", type=int, default=640, help="320 o 480 sui PC senza GPU")
    p.add_argument("--persone", type=int, default=2, help="persone considerate")
    p.add_argument("--altezza-min", type=float, default=0.25,
                   help="altezza minima di una persona, in frazione dell'immagine")
    p.add_argument("--attesa", type=float, default=2.0, help="secondi di conferma di un allarme")
    p.add_argument("--tolleranza", type=float, default=15.0, help="pixel tra piedi e zona vietata")
    p.add_argument("--tag-family", default="tag25h9", help="famiglia delle AprilTag")
    p.add_argument("--margine-tag", type=float, default=0.03,
                   help="margine del corridoio oltre i tag, in frazione della larghezza")
    p.add_argument("--rete-attesa", type=float, default=3.0,
                   help="secondi senza rete, con i tag in vista, prima dell'allarme")
    p.add_argument("--mqtt", default="auto",
                   help="broker MQTT: auto (su questo PC, avviato se serve) o un indirizzo")
    p.add_argument("--no-mqtt", action="store_true", help="demo senza orologio")
    p.add_argument("--no-intro", action="store_true", help="salta la schermata iniziale")
    p.add_argument("--schermo-intero", action="store_true", help="parte a schermo intero")
    return p.parse_args()


def show_intro() -> None:
    """Schermata di presentazione dell'interfaccia di volo (Avanti per
    proseguire), la stessa che compare all'avvio del drone."""
    try:
        import pygame
        from drone.ui.setup.welcome import show_welcome_screen
    except Exception as exc:
        print(f"Schermata iniziale non disponibile ({exc})")
        return
    pygame.init()
    try:
        pygame.display.set_caption(WINDOW)
        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        if not show_welcome_screen(screen):
            sys.exit("Uscita dalla schermata iniziale")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Schermata iniziale non riuscita ({exc})")
    finally:
        pygame.display.quit()


class AlertLog:
    """Righe del log degli alert, nel formato di console.print_event:
    ora, simbolo di gravità, messaggio."""

    def __init__(self) -> None:
        self.lines: deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()

    def add(self, glyph: str, message: str) -> None:
        with self._lock:
            self.lines.append(f"{time.strftime('%H:%M:%S')}  {glyph}  {message}")

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.lines)


def log_alert(log: AlertLog, alert: dict) -> None:
    """Come drone/flight/steps.py: nel log vanno i pericoli, non i messaggi
    che spengono o aggiornano un allarme."""
    kind = alert["type"]
    if kind == "safety_net_missing":
        log.add("×", alert["message"])
    elif kind == "safety_net_ok":
        log.add("•", alert["message"])
    elif kind not in ("dpi_ok", "dpi_missing_update", "restricted_area_ok"):
        log.add("!", alert.get("message") or alert.get("title") or kind)


def row(label: str, value: str, warn: bool = False, glyph: str = "·") -> str:
    """Riga del pannello di stato, nello stesso formato del log."""
    return f"{label[:9]:<10}{'!' if warn else glyph}  {value}"


def number(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def status_rows(view, source, worker, bank, params, broker, link) -> list[str]:
    """Pannello "Stato demo": prima quello che serve controllare all'avvio
    (sorgente, velocità, orologio e rete), poi le impostazioni."""
    detail = source.description
    if view == "drone":
        detail = f"drone {source.status}"
        if source.battery is not None:
            detail += f", batteria {source.battery}%"
    rows = [
        row("Sorgente", detail, view == "drone" and source.status != "collegato"),
        row("Analisi", f"{number(worker.fps, 0)} FPS su {bank.device_name}", worker.fps < 8),
        row("Video", f"{number(source.fps, 0)} FPS"),
    ]
    if link.enabled:
        state = "collegato" if link.watch_state()["connected"] else "in attesa"
        mission = "missione avviata" if link.mission else "missione ferma"
        rows.append(row("Orologio", f"{state}, {mission}", not link.connected))
    else:
        rows.append(row("Orologio", "senza MQTT", True))
    if broker is not None:
        rows.append(row("Rete", broker.lan, not broker.lan_ok))
        rows.append(row("Broker", broker.state, broker.process is None and broker.state != "attivo"))
    if not params["corridor"]:
        corridor = "spento"
    else:
        tags = worker.surveillance[view].tags
        corridor = ("non disponibile" if tags.detector is None
                    else "attivo" if tags.corridor else "nessun tag in vista")
    models = ", ".join(ROLE_NAMES[n] for n, _m in bank.enabled()) or "nessuno"
    rows += [
        row("Modelli", models, not bank.enabled()),
        row("Sorveglia", "attiva" if params["surveillance"] else "spenta", not params["surveillance"]),
        row("Soglia", number(params["conf"])),
        row("Persone", f"{params['max_people']}, altezza minima {number(params['min_h'])}"),
        row("Corridoio", corridor),
    ]
    return rows


def main() -> None:
    args = parse_args()
    enable_dpi_awareness()

    # Modelli e webcam si preparano mentre è aperta la schermata iniziale.
    ready: dict = {}

    def prepare() -> None:
        try:
            wanted = [ROLES[r.strip()] for r in args.modelli.split(",") if r.strip() in ROLES]
            ready["bank"] = ModelBank(wanted, args.imgsz)
            ready["webcam"] = WebcamSource(args.sorgente)
        except Exception as exc:
            ready["error"] = exc

    preparing = threading.Thread(target=prepare, daemon=True)
    preparing.start()
    if not args.no_intro:
        show_intro()
    preparing.join()
    if "error" in ready:
        sys.exit(f"Avvio non riuscito: {ready['error']}")
    bank = ready["bank"]

    sources = {"webcam": ready["webcam"]}
    if args.drone is not None:
        host = None if args.drone == "auto" else args.drone
        rgb = True
        try:
            from drone.config import APP_CONFIG
            host = host or APP_CONFIG.tello_host
            rgb = bool(APP_CONFIG.frame_from_controller_is_rgb)
        except Exception:
            pass
        sources["drone"] = DroneSource(host, rgb)

    log = AlertLog()
    broker = None
    mqtt_host = None
    if not args.no_mqtt:
        mqtt_host = args.mqtt
        if mqtt_host in ("auto", "localhost", "127.0.0.1"):
            broker = Broker()
            mqtt_host = "127.0.0.1" if broker.ensure() else None
    link = WatchLink(mqtt_host, log.add)

    params = {"conf": args.conf, "max_people": args.persone, "min_h": args.altezza_min,
              "surveillance": True, "corridor": True}
    zones: dict = {}
    worker = VisionWorker(bank, sources, params, zones, args)
    worker.start()

    view = "webcam"
    drag = {"start": None}

    def on_mouse(event, fx, fy, inside) -> None:
        # Zona vietata disegnata col mouse, in coordinate dell'immagine analizzata.
        if event == cv2.EVENT_LBUTTONDOWN and inside:
            drag["start"] = (fx, fy)
        elif event == cv2.EVENT_MOUSEMOVE and drag["start"]:
            x0, y0 = drag["start"]
            zones[view] = (min(x0, fx), min(y0, fy), max(x0, fx), max(y0, fy))
        elif event == cv2.EVENT_LBUTTONUP:
            drag["start"] = None
        elif event == cv2.EVENT_RBUTTONDOWN and inside:
            zones.pop(view, None)

    screen = Screen(WINDOW, args.schermo_intero, on_mouse)
    status_mode = 0  # 0 nessun pannello, 1 stato, 2 comandi
    status_cache, status_at = [], 0.0
    toast = None

    def say(key: str, message: str) -> None:
        nonlocal toast
        toast = (key, message, time.monotonic() + 2.5)

    if "drone" in sources:
        say("W", "Visuale drone con il tasto W")
    last_drawn, drawn_at = None, 0.0

    try:
        while True:
            while not worker.alerts.empty():
                _src, alert = worker.alerts.get_nowait()
                log_alert(log, alert)
                link.alarm(alert)
            while not bank.messages.empty():
                say("OK", bank.messages.get_nowait())

            source = sources[view]
            frame, seq = source.latest()
            result = worker.result(view)
            # Si ridisegna quando arriva un'immagine o un risultato nuovo, e
            # comunque almeno 10 volte al secondo per log, orologio e messaggi.
            drawn = (view, seq, result["seq"] if result else None)
            if drawn == last_drawn and time.monotonic() - drawn_at < 0.1:
                key = cv2.waitKey(5) & 0xFF
                if key == 255:
                    continue
            else:
                last_drawn, drawn_at = drawn, time.monotonic()
                key = None
            if status_mode == 1 and time.monotonic() - status_at > 0.5:
                status_cache = status_rows(view, source, worker, bank, params, broker, link)
                status_at = time.monotonic()
            status = (status_cache if status_mode == 1 else
                      [row(k, d, glyph="•") for k, d in COMMANDS] if status_mode == 2 else None)

            enabled = [ROLE_NAMES[n] for n, _m in bank.enabled()]
            if not params["surveillance"]:
                detail, warn = "sorveglianza spenta", True
            else:
                detail, warn = ("riconoscimento di " + ", ".join(enabled)) if enabled else \
                    "nessun modello acceso", not enabled
            if view == "drone" and getattr(source, "battery", None) is not None:
                detail += f"  ·  batteria {source.battery}%"

            # Il pannello dell'orologio resta visibile anche senza broker, con il
            # motivo; sparisce solo con --no-mqtt.
            if link.enabled:
                watch = link.watch_state()
            elif args.no_mqtt:
                watch = None
            else:
                watch = {"connected": False, "bpm": None, "spo2": None,
                         "note": "Broker MQTT non attivo (tasto H)"}

            if key is None:
                screen.render(ViewState(
                    frame=frame,
                    placeholder=("Collegamento al drone: " + source.status) if view == "drone"
                    else "In attesa della webcam",
                    result=result,
                    styles={n: m for n, m in bank.enabled()},
                    zone=zones.get(view),
                    title="Visuale drone" if view == "drone" else "Visuale webcam",
                    detail=detail,
                    detail_warn=warn,
                    watch=watch,
                    log=log.snapshot(),
                    status_title="Stato demo" if status_mode == 1 else "Comandi demo",
                    status=status,
                    toast=toast[:2] if toast and time.monotonic() < toast[2] else None,
                    net_missing=bool(result and result.get("net_missing")),
                ))

                key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27) or screen.closed():
                break
            if key == 255:
                continue
            if key == ord("h"):
                status_mode = (status_mode + 1) % 3
                status_at = 0.0
            elif key == ord("s"):
                params["surveillance"] = not params["surveillance"]
                say("S", f"Sorveglianza {'accesa' if params['surveillance'] else 'spenta'}")
            elif key in (ord("+"), ord("=")):
                params["conf"] = min(0.95, params["conf"] + 0.05)
                say("+", f"Soglia di confidenza {number(params['conf'])}")
            elif key == ord("-"):
                params["conf"] = max(0.05, params["conf"] - 0.05)
                say("-", f"Soglia di confidenza {number(params['conf'])}")
            elif key == ord("p"):
                params["max_people"] = params["max_people"] % 3 + 1
                say("P", f"Persone considerate: {params['max_people']}")
            elif key == ord("a"):
                params["min_h"] = round(params["min_h"] + 0.05, 2) if params["min_h"] < 0.6 else 0.0
                say("A", f"Altezza minima persona: {number(params['min_h'])}")
            elif key == ord("t"):
                params["corridor"] = not params["corridor"]
                say("T", f"Corridoio AprilTag {'acceso' if params['corridor'] else 'spento'}")
            elif key == ord("r"):
                worker.surveillance[view].tags.reset()
                say("R", "Corridoio azzerato: lo ricalcolo dai tag")
            elif key == ord("w"):
                if "drone" in sources:
                    view = "drone" if view == "webcam" else "webcam"
                    worker.active = view
                    say("W", "Visuale drone" if view == "drone" else "Visuale webcam")
                else:
                    say("W", "Drone non attivo: avviare con --drone")
            elif key == ord("c"):
                ok = view == "webcam" and sources["webcam"].next_camera()
                say("C", "Altra webcam" if ok else "Nessun'altra webcam disponibile")
            elif key == ord("m"):
                if link.enabled:
                    link.set_mission(not link.mission)
                    say("M", f"Missione dell'orologio {'avviata' if link.mission else 'ferma'}")
                else:
                    say("M", "Orologio non attivo")
            elif key == ord("f"):
                screen.toggle_fullscreen()
            elif key in KEY_MODELS:
                say(chr(key), bank.toggle(KEY_MODELS[key]))
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        for src in sources.values():
            src.close()
        link.close()
        if broker is not None:
            broker.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
