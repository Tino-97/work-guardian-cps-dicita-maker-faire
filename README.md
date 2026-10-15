<p align="center">
  <img src="assets/work-guardian-github.png" alt="Work-Guardian" width="100%">
</p>

# Work-Guardian

**Work-Guardian** è un Cyber-Physical-Human System (CPHS) per la sicurezza nei cantieri con lavorazioni in quota. Il sistema integra un drone DJI Tello EDU, uno smartwatch biometrico e una stazione centrale per monitorare l'ambiente e l'operatore, rilevare situazioni di rischio e inviare segnalazioni in tempo reale.

Progetto sviluppato per il corso di Cyber Physical Systems dell'Università degli Studi Roma Tre, A.A. 2025/2026.

## Funzionalità principali

- il drone vola in autonomia lungo un percorso a waypoint, guidato dalla posizione che la stazione centrale ricava dai marker AprilTag e stabilizza con un filtro di Kalman;
- nei punti di supervisione il drone si ferma e i modelli YOLO analizzano le immagini della camera;
- il sistema verifica che l'operatore indossi elmetto, occhiali, gilet e scarpe antinfortunistiche;
- il sistema controlla la presenza delle reti di sicurezza;
- il sistema rileva gli ingressi nelle aree interdette e le cadute;
- lo smartwatch misura frequenza cardiaca e SpO₂ e segnala i valori anomali;
- gli allarmi compaiono sullo schermo della stazione centrale, dove li vede il supervisore; quelli che riguardano l'operatore arrivano anche allo smartwatch, che li segnala con display, LED e vibrazione.

## Architettura

```mermaid
flowchart LR
    D[DJI Tello EDU] <-->|video e comandi di volo| PC[Stazione centrale]
    W[Smartwatch] <-->|dati biometrici e allarmi| B[Broker Mosquitto]
    B <--> PC

    PC --> V[Visione artificiale]
    V --> S[Supervisione e gestione allarmi]
    S --> B
```

Il drone trasmette alla stazione centrale il video della camera e riceve da essa i comandi di volo. La stazione analizza le immagini con i modelli di visione artificiale e, quando rileva una situazione di rischio, genera un allarme. Attraverso il broker, lo smartwatch invia alla stazione i parametri biometrici dell'operatore e riceve gli allarmi per i DPI mancanti e per le aree interdette.

## Componenti

| Componente | Funzione |
|---|---|
| **DJI Tello EDU** | Volo lungo il percorso e acquisizione delle immagini dell'area di lavoro |
| **Smartwatch biometrico** | Arduino Nano ESP32 con sensore MAX30100: misura dei parametri biometrici e segnalazione degli allarmi all'operatore |
| **Stazione centrale** | PC che guida il drone, esegue i modelli di visione artificiale e gestisce gli allarmi |
| **Mosquitto / MQTT** | Comunicazione tra stazione centrale e smartwatch |

## Struttura della repository

```text
work-guardian-cps-dicita/
├── communication/    # Configurazione di rete e broker MQTT
├── drone/            # Applicazione principale e gestione del drone
├── vision/           # Modelli YOLO e strumenti per training e test
├── wearable/         # Firmware e risorse dello smartwatch
├── requirements.txt  # Dipendenze Python
└── README.md
```

Le cartelle `communication`, `vision` e `wearable` contengono un file `info.txt` che ne descrive il contenuto.

## Avvio rapido

Per volare servono il drone DJI Tello EDU, che si collega alla stazione centrale tramite il proprio Wi-Fi, e un controller PS4. Lo smartwatch e il broker Mosquitto sono necessari per misurare i parametri biometrici dell'operatore e per avvisarlo degli allarmi.

Clonare la repository e installare le dipendenze Python:

```bash
git clone https://github.com/faiellaemanuele/work-guardian-cps-dicita.git
cd work-guardian-cps-dicita
pip install -r requirements.txt
```

Configurare la rete e il broker MQTT seguendo la documentazione in [`communication/docs/communication_setup.md`](communication/docs/communication_setup.md), poi avviare l'applicazione principale:

```bash
python drone/main.py
```

## Autori

- **Michele Arietta**
- **Aurora Camusi**
- **Antonio Di Sabatino**
- **Emanuele Faiella Lauricella**
- **Riccardo Magnanelli**
- **Federico Pace**
- **Eleonora Ramunno**

## Nota

Work-Guardian è un **prototipo accademico** sviluppato a fini didattici e sperimentali. Non è un sistema di sicurezza certificato.
