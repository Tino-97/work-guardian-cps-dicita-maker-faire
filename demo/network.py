"""Collegamento con l'orologio: broker MQTT, controlli di rete e messaggi.

L'orologio cerca il broker all'indirizzo scritto nel firmware
(MQTT_BROKER_IP in wearable/smartwatch/network_config.h). Qualunque PC può
fare da stazione centrale se ha quell'indirizzo sulla scheda Ethernet
collegata al router: lo imposta demo/prepara_pc.bat. La demo poi avvia da
sola Mosquitto con la configurazione del progetto, se non è già attivo, e
controlla che l'orologio possa raggiungerlo.

close_mission() chiude la missione rimasta attiva sull'orologio
(demo/chiudi_missione.bat).
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
WATCH_NETWORK_CONFIG = ROOT / "wearable" / "smartwatch" / "network_config.h"
BROKER_CONFIG = ROOT / "communication" / "broker" / "mqtt_broker.conf"
MQTT_PORT = 1883

TOPIC_ALARMS = "cantiere/allarmi"
TOPIC_WATCH = "cantiere/sensori/orologio"

# Stessa corrispondenza di drone/flight/steps.py: valore del campo "tipo"
# riconosciuto dal firmware. La caduta e la rete non vanno all'orologio.
WATCH_TYPES = {
    "dpi_missing": "DPI_MANCANTE",
    "dpi_missing_update": "DPI_MANCANTE",
    "dpi_ok": "DPI_OK",
    "restricted_area": "AREA_VIETATA",
    "restricted_area_ok": "AREA_OK",
}
# Livelli come _ALARM_LEVELS di drone/flight/steps.py.
LEVELS = {"restricted_area": "critical", "restricted_area_ok": "info", "fall": "critical",
          "dpi_missing": "warning", "dpi_missing_update": "warning", "dpi_ok": "info",
          "safety_net_missing": "critical", "safety_net_ok": "info"}


def watch_broker_ip() -> str:
    """Indirizzo del broker scritto nel firmware dell'orologio."""
    try:
        text = WATCH_NETWORK_CONFIG.read_text(encoding="utf-8", errors="ignore")
        found = re.search(r'MQTT_BROKER_IP\s*=\s*"([0-9.]+)"', text)
        if found:
            return found.group(1)
    except OSError:
        pass
    return "192.168.1.2"


def local_ipv4() -> set[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return ips


def port_open(host: str, port: int = MQTT_PORT, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_mosquitto() -> Optional[str]:
    found = shutil.which("mosquitto")
    if found:
        return found
    default = Path(r"C:\Program Files\Mosquitto\mosquitto.exe")
    return str(default) if default.exists() else None


class Broker:
    """Broker MQTT locale: se non c'è già lo avvia la demo con la
    configurazione del progetto e lo chiude all'uscita."""

    def __init__(self) -> None:
        self.watch_ip = watch_broker_ip()
        self.process: Optional[subprocess.Popen] = None
        self.state = "non controllato"
        self.lan = "non controllata"
        self.lan_ok = False

    def ensure(self) -> bool:
        if port_open("127.0.0.1"):
            self.state = "attivo"
        else:
            exe = find_mosquitto()
            if exe is None or not BROKER_CONFIG.exists():
                self.state = "Mosquitto non installato"
                self.check_lan()
                return False
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                self.process = subprocess.Popen(
                    [exe, "-c", str(BROKER_CONFIG)], cwd=str(ROOT),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            except OSError as exc:
                self.state = f"avvio non riuscito ({exc})"
                return False
            for _ in range(30):
                if port_open("127.0.0.1"):
                    break
                time.sleep(0.1)
            else:
                self.state = "avvio non riuscito"
                return False
            self.state = "avviato dalla demo"
        print(f"Broker MQTT {self.state}")
        self.check_lan()
        return True

    def check_lan(self) -> None:
        """Controlla che l'orologio possa raggiungere il broker."""
        ip = self.watch_ip
        self.lan_ok = False
        if ip not in local_ipv4():
            self.lan = f"{ip} assente, eseguire prepara_pc.bat"
            hint = (f"questo PC non ha l'indirizzo {ip} che l'orologio usa per il broker: "
                    "eseguire demo/prepara_pc.bat (vedi demo/info.txt)")
        elif not port_open(ip):
            self.lan = "broker solo locale, net stop mosquitto"
            hint = ("il broker accetta solo connessioni da questo PC: fermare il servizio "
                    "Mosquitto (net stop mosquitto) e riavviare la demo")
        else:
            self.lan = f"orologio può collegarsi a {ip}"
            self.lan_ok = True
            hint = self.lan
        print(f"Rete dell'orologio: {hint}")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


def _mqtt_client(client_id: str):
    """Client paho-mqtt, versione 2 o 1."""
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


class WatchLink:
    """Client MQTT verso l'orologio, con lo stesso formato dei messaggi del
    drone (drone/perception/vision_loop.py)."""

    def __init__(self, host: Optional[str], on_alert: Callable[[str, str], None]) -> None:
        self.client = None
        self.connected = False
        self.mission = False
        self.watch: Optional[dict] = None  # ultima telemetria dell'orologio
        self.on_alert = on_alert
        if not host:
            return
        try:
            self.client = _mqtt_client("wg-demo")
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.client.connect_async(host, MQTT_PORT)
            self.client.loop_start()
            # L'orologio ignora gli allarmi fuori missione: la demo la avvia.
            self.mission = True
        except Exception as exc:
            print(f"MQTT non disponibile ({exc}): la demo prosegue senza orologio")
            self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _on_connect(self, client, *_args) -> None:
        self.connected = True
        client.subscribe(TOPIC_WATCH)
        self._send_mission()

    def _on_disconnect(self, *_args) -> None:
        self.connected = False

    def _on_message(self, _client, _userdata, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        if "lettura_valida" in data or ("bpm" in data and "evento" not in data):
            self.watch = {"t": time.monotonic(), **data}
        # Come in volo: l'allarme biometrico dell'orologio va nel log degli alert.
        if data.get("evento") == "BIOMETRIA_ANOMALA":
            bpm = "--" if data.get("bpm") is None else data.get("bpm")
            spo2 = "--" if data.get("spo2") is None else f"{data.get('spo2')}%"
            self.on_alert("!", f"Orologio: {bpm} bpm, SpO2 {spo2}")

    def watch_state(self) -> dict:
        w = self.watch
        fresh = w is not None and time.monotonic() - w["t"] < 3.0
        return {"connected": fresh,
                "bpm": w.get("bpm") if fresh else None,
                "spo2": w.get("spo2") if fresh else None}

    def _publish(self, payload: dict, retain: bool = False):
        if self.client is not None:
            return self.client.publish(TOPIC_ALARMS, json.dumps(payload), qos=1, retain=retain)
        return None

    def _send_mission(self):
        # Messaggio conservato dal broker (retain): un orologio che si accende
        # o si ricollega dopo l'avvio della demo lo riceve subito.
        tipo = "AVVIO_MISSIONE" if self.mission else "FINE_MISSIONE"
        return self._publish({"source": "drone", "tipo": tipo}, retain=True)

    def set_mission(self, active: bool):
        self.mission = active
        return self._send_mission()

    def alarm(self, alert: dict) -> None:
        kind = alert.get("type", "surveillance")
        payload = {"source": "drone", "type": kind, "level": LEVELS.get(kind, "warning"),
                   "msg": alert.get("message", "")}
        if kind in WATCH_TYPES:
            payload["tipo"] = WATCH_TYPES[kind]
        if alert.get("missing"):
            payload["dettaglio"] = ", ".join(str(n).capitalize() for n in alert["missing"])
        self._publish(payload)

    def close(self) -> None:
        if self.client is None:
            return
        info = self.set_mission(False)
        # La fine missione deve arrivare al broker prima che la demo lo chiuda,
        # altrimenti l'orologio resta in missione anche dopo un riavvio.
        if info is not None and self.connected:
            try:
                info.wait_for_publish(timeout=2.0)
            except Exception:
                pass
        # Lascia all'orologio, se collegato, il tempo di riceverla.
        time.sleep(1.0)
        self.client.loop_stop()
        self.client.disconnect()


def close_mission(host: str = "auto", wait: float = 120.0) -> int:
    """Chiude la missione rimasta attiva sull'orologio.

    L'orologio ricorda la missione anche dopo un riavvio e il broker conserva
    l'ultimo messaggio di missione: se la demo o il programma del drone si
    chiudono senza mandare FINE_MISSIONE, l'orologio resta in missione. Qui la
    si manda come messaggio conservato, come fa il drone all'atterraggio, e si
    aspetta che l'orologio la confermi."""
    broker = None
    if host in ("auto", "localhost", "127.0.0.1"):
        broker = Broker()
        if not broker.ensure():
            print(f"Broker non disponibile: {broker.state}")
            return 1
        host = "127.0.0.1"
    state: dict = {"connected": False, "watch": None}

    def on_connect(client, *_a) -> None:
        state["connected"] = True
        client.subscribe(TOPIC_WATCH)

    def on_message(_c, _u, msg) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(data, dict) and "stato" in data:
            state["watch"] = data["stato"]

    try:
        client = _mqtt_client("wg-chiudi-missione")
        client.on_connect, client.on_message = on_connect, on_message
        client.connect(host, MQTT_PORT)
    except ImportError:
        print("Manca paho-mqtt: eseguire demo/prepara_pc.bat")
        return 1
    except OSError as exc:
        print(f"Broker {host} non raggiungibile ({exc})")
        return 1
    client.loop_start()
    code, last = 1, None
    try:
        deadline = time.monotonic() + 5
        while not state["connected"] and time.monotonic() < deadline:
            time.sleep(0.1)
        if not state["connected"]:
            print(f"Broker {host} non risponde")
            return 1
        client.publish(TOPIC_ALARMS, json.dumps({"source": "drone", "tipo": "FINE_MISSIONE"}),
                       qos=1, retain=True).wait_for_publish(5)
        print("FINE_MISSIONE inviata. Accendere l'orologio, se è spento: la riceve appena si collega.")
        if broker is not None and not broker.lan_ok:
            print(f"Attenzione, rete dell'orologio: {broker.lan}")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            current = state["watch"]
            if current is not None and current != last:
                print(f"Orologio collegato, stato: {current}")
                last = current
            if current in ("MISSIONE_NON_AVVIATA", "GUASTO"):
                print("Missione chiusa sull'orologio.")
                code = 0
                break
            time.sleep(0.2)
        else:
            print(f"Nessuna risposta dall'orologio in {wait:.0f} s: accenderlo, controllare che sia "
                  "sulla rete del router e riprovare." if last is None else
                  "L'orologio risulta ancora in missione: riavviarlo (due pressioni lunghe) e riprovare.")
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        if broker is not None:
            broker.stop()
    return code
