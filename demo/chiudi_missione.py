"""Chiude la missione dell'orologio rimasta attiva.

L'orologio ricorda la missione anche dopo un riavvio (la salva in memoria) e il
broker conserva l'ultimo messaggio di missione: se il programma del drone o la
demo si chiudono senza mandare FINE_MISSIONE, l'orologio resta in missione.
Questo script manda FINE_MISSIONE come messaggio conservato, lo stesso del
drone all'atterraggio, e aspetta che l'orologio lo confermi.

Uso, dalla radice del progetto:

    python demo/chiudi_missione.py              broker su questo PC, avviato se serve
    python demo/chiudi_missione.py --broker IP  broker su un altro PC
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from network import MQTT_PORT, TOPIC_ALARMS, TOPIC_WATCH, Broker  # noqa: E402

END = {"source": "drone", "tipo": "FINE_MISSIONE"}
OUT_OF_MISSION = {"MISSIONE_NON_AVVIATA", "GUASTO"}


def main() -> int:
    p = argparse.ArgumentParser(description="Chiude la missione dell'orologio")
    p.add_argument("--broker", default="auto",
                   help="auto (broker su questo PC, avviato se serve) o indirizzo del broker")
    p.add_argument("--attesa", type=float, default=120.0,
                   help="secondi di attesa della conferma dell'orologio")
    args = p.parse_args()

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("Manca paho-mqtt: pip install -r requirements.txt")
        return 1

    broker = None
    host = args.broker
    if host in ("auto", "localhost", "127.0.0.1"):
        broker = Broker()
        if not broker.ensure():
            print(f"Broker non disponibile: {broker.state}")
            return 1
        host = "127.0.0.1"

    state = {"connected": False, "watch": None}

    def on_connect(client, *_a):
        state["connected"] = True
        client.subscribe(TOPIC_WATCH)

    def on_message(_c, _u, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(data, dict) and "stato" in data:
            state["watch"] = data["stato"]

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wg-chiudi-missione")
    except AttributeError:  # paho-mqtt 1.x
        client = mqtt.Client(client_id="wg-chiudi-missione")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, MQTT_PORT)
    except OSError as exc:
        print(f"Broker {host} non raggiungibile ({exc})")
        return 1
    client.loop_start()

    code = 1
    try:
        for _ in range(50):
            if state["connected"]:
                break
            time.sleep(0.1)
        if not state["connected"]:
            print(f"Broker {host} non risponde")
            return 1

        client.publish(TOPIC_ALARMS, json.dumps(END), qos=1, retain=True).wait_for_publish(5)
        print("FINE_MISSIONE inviata e conservata dal broker.")
        print("Accendere l'orologio, se è spento: la riceve appena si collega.")
        if broker is not None and not broker.lan_ok:
            print(f"Attenzione, rete dell'orologio: {broker.lan}")

        deadline = time.monotonic() + args.attesa
        last = None
        while time.monotonic() < deadline:
            current = state["watch"]
            if current != last and current is not None:
                print(f"Orologio collegato, stato: {current}")
                last = current
            if current in OUT_OF_MISSION:
                print("Missione chiusa sull'orologio.")
                code = 0
                break
            time.sleep(0.2)
        else:
            if last is None:
                print(f"Nessuna risposta dall'orologio in {args.attesa:.0f} s: accenderlo, "
                      "controllare che sia sulla rete del router e rilanciare lo script.")
            else:
                print("L'orologio è collegato ma risulta ancora in missione: riavviarlo "
                      "(due pressioni lunghe) e rilanciare lo script.")
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        if broker is not None:
            broker.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
