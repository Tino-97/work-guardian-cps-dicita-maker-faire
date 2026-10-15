/*
 * Work Guardian - Firmware smartwatch
 * Arduino Nano ESP32 + MAX30100 + LCD 16x2
 *
 * Collegamenti principali:
 *   LCD 16x2 : RS=D7, EN=D8, D4..D7=A0..A3, RW=GND
 *   MAX30100 : VIN=3V3, GND, SDA=A4, SCL=A5
 *   Vibrazione=D2, pulsante=D3, LED blu=D4, verde=D5, rosso=D6
 *
 * Funzioni principali:
 * - monitoraggio di frequenza cardiaca e SpO2 con filtraggio dei campioni;
 * - macchina a stati finiti per missione, verifica, allarme e guasto;
 * - ricezione degli allarmi dal drone tramite MQTT;
 * - invio periodico della telemetria biometrica;
 * - segnalazione locale tramite LCD, LED RGB e vibrazione;
 * - persistenza dello stato di missione attraverso i riavvii.
 *
 * Stati FSM:
 *   z0 MISSIONE_NON_AVVIATA
 *   z1 RICERCA_SEGNALE
 *   z2 NORMALE
 *   z3 VERIFICA
 *   z4 ALLARME
 *   z5 SILENZIATO
 *   z6 GUASTO
 *
 * Pulsante:
 * - pressione breve: silenzia un allarme; fuori missione abilita/disabilita
 *   la prova del sensore;
 * - due pressioni lunghe: riavvio del dispositivo.
 */

// ==================== Librerie ====================
#include <Wire.h>
#include <LiquidCrystal.h>
#include "MAX30100_PulseOximeter.h"
#include <WiFi.h>
#include <PubSubClient.h>
#include <lwip/sockets.h>
#include <Preferences.h>

// ==================== Modalità operative ====================
const bool BYPASS_MISSION_WAIT = false;

const bool SERIAL_PLOTTER = false;

// ==================== Hardware ====================
const int LCD_RS = 7, LCD_EN = 8, LCD_D4 = A0, LCD_D5 = A1, LCD_D6 = A2, LCD_D7 = A3;
LiquidCrystal lcd(LCD_RS, LCD_EN, LCD_D4, LCD_D5, LCD_D6, LCD_D7);

const int PIN_VIBRATION = 2;
const int PIN_BUTTON    = 3;
const int PIN_LED_BLUE  = 4;
const int PIN_LED_GREEN = 5;
const int PIN_LED_RED   = 6;

PulseOximeter pox;

const LEDCurrent IR_LED_CURRENT = MAX30100_LED_CURR_27_1MA;

bool sensorPresent = false;
const int SENSOR_MAX_ATTEMPTS = 10;
const unsigned long SENSOR_RETRY_INTERVAL_MS = 5000;
unsigned long lastSensorRetryMs = 0;

const unsigned long SENSOR_CHECK_INTERVAL_MS = 2000;
const int SENSOR_MAX_MISSED_CHECKS = 2;
unsigned long lastSensorCheckMs = 0;
int missedSensorChecks = 0;

// ==================== Missione e persistenza ====================
Preferences prefs;

bool missionActive = false;

bool sensorTestActive = false;

// ==================== Rete e MQTT ====================
#include "network_config.h"
const int   MQTT_PORT   = 1883;

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

const char* TELEMETRY_TOPIC = "cantiere/sensori/orologio";
const char* PRESENCE_TOPIC  = "cantiere/sistema/orologio/status";
const char* ALARM_TOPIC     = "cantiere/allarmi";

const unsigned long PUBLISH_INTERVAL_MS       = 500;
const unsigned long NETWORK_RETRY_INTERVAL_MS = 5000;

const unsigned long WIFI_CONNECT_TIMEOUT_MS = 20000;
unsigned long lastPublishMs      = 0;
unsigned long lastNetworkRetryMs = 0;
unsigned long wifiAttemptStartMs = 0;
bool          wifiAttemptStarted = false;

const unsigned long NETWORK_LOSS_GRACE_MS = 15000;
bool          brokerWasConnected  = false;
bool          brokerEverConnected = false;
unsigned long brokerLostSinceMs   = 0;
bool          networkReportedDown = true;

const char*   MQTT_CLIENT_ID = "wg-orologio";
volatile bool brokerConnectRunning  = false;
volatile bool brokerConnectFinished = false;
volatile bool brokerConnectOk       = false;

const unsigned long BROKER_STALL_MS = 5000;
unsigned long brokerWritableSinceMs = 0;

bool biometricAlarmPending = false;
char pendingAlarmBpm[8]  = "null";
char pendingAlarmSpo2[8] = "null";

// ==================== Gestione degli allarmi ====================
bool biometricCauseActive = false;
bool ppeCauseActive = false;
bool restrictedAreaCauseActive = false;
bool isAnyCauseActive() { return biometricCauseActive || ppeCauseActive || restrictedAreaCauseActive; }

String missingPpeList = "";

// ==================== Interfaccia utente ====================
const int CAUSE_NONE            = -1;
const int CAUSE_BIOMETRIC       = 0;
const int CAUSE_RESTRICTED_AREA = 1;
const int CAUSE_PPE             = 2;

const unsigned int  LCD_COLUMNS           = 16;
const unsigned long CAUSE_ROTATION_MS     = 4000;
const unsigned long SCROLL_STEP_MS        = 450;
const unsigned long SCROLL_START_PAUSE_MS = 1500;

const unsigned long AREA_VIBRATION_ON_MS     = 250;
const unsigned long AREA_VIBRATION_PERIOD_MS = 500;
const unsigned long PPE_VIBRATION_ON_MS      = 300;
const unsigned long PPE_VIBRATION_PERIOD_MS  = 1500;

int displayedCause = CAUSE_NONE;
unsigned long displayedCauseSinceMs = 0;
int prevCauseMask = 0;

int vibrationCause = CAUSE_NONE;
unsigned long vibrationCauseSinceMs = 0;

unsigned long feedbackPulseStartMs = 0;
unsigned long feedbackPulseMs      = 0;

const unsigned long NOTICE_MS = 3000;
const char*   noticeLine1   = nullptr;
const char*   noticeLine2   = "";
unsigned long noticeStartMs = 0;

// ==================== Soglie biometriche ====================
const int BPM_MIN_IN  = 50;
const int BPM_MAX_IN  = 120;
const int SPO2_MIN_IN = 92;

const int BPM_MIN_OUT  = 55;
const int BPM_MAX_OUT  = 115;
const int SPO2_MIN_OUT = 94;

const unsigned long PERSISTENCE_MS        = 3000;
const unsigned long SILENCE_TIMEOUT_MS    = 30000;
const unsigned long HR_STALE_TIMEOUT_MS   = 5000;

// ==================== Macchina a stati ====================
enum FsmState {
  STATE_MISSION_NOT_STARTED,
  STATE_SEARCHING_SIGNAL,
  STATE_NORMAL,
  STATE_VERIFYING,
  STATE_ALARM,
  STATE_SILENCED,
  STATE_FAULT
};

FsmState currentState = STATE_MISSION_NOT_STARTED;

FsmState    loggedState     = STATE_MISSION_NOT_STARTED;
const char* loggedStateName = "";

unsigned long verifyStartMs  = 0;
unsigned long silenceStartMs  = 0;

bool maskedCriticalPending = false;
unsigned long maskedCriticalStartMs = 0;

// ==================== Acquisizione e filtraggio ====================
bool          newBeat = false;
unsigned long lastBeatMs = 0;

unsigned long beatIntervalMs = 0;

int  beatsSinceSpo2 = 0;
bool newSpo2 = false;

// Callback del MAX30100: aggiorna intervallo tra battiti e cadenza di calcolo della SpO2.
void onBeatDetected() {
  unsigned long nowMs = millis();
  beatIntervalMs = nowMs - lastBeatMs;
  lastBeatMs = nowMs;
  newBeat = true;

  if (beatIntervalMs > BEATDETECTOR_INVALID_READOUT_DELAY) beatsSinceSpo2 = 0;
  if (++beatsSinceSpo2 >= CALCULATE_EVERY_N_BEATS) {
    beatsSinceSpo2 = 0;
    newSpo2 = true;
  }
}

const int   HR_MEDIAN_WINDOW   = 5;
const int   SPO2_MEDIAN_WINDOW = 5;

const float HR_EMA_ALPHA   = 0.35;
const float SPO2_EMA_ALPHA = 0.30;
const float HR_MAX_DEVIATION_RATIO = 0.25;

const float HR_MIN_PLAUSIBLE = 60000.0 / HR_STALE_TIMEOUT_MS;
const float HR_MAX_PLAUSIBLE = 220;

const int HR_MAX_REJECTIONS = 6;
float hrRejected[HR_MAX_REJECTIONS];
int   hrRejectedCount = 0;

const int SPO2_MAX_OUT_OF_RANGE = 2;

const float SPO2_LOCK_TOLERANCE = 2;
int consecutiveSpo2OutOfRange = 0;

float hrBuffer[HR_MEDIAN_WINDOW];
int   hrBufferIndex = 0, hrBufferCount = 0;

float spo2Buffer[SPO2_MEDIAN_WINDOW];
int   spo2BufferIndex = 0, spo2BufferCount = 0;

float hrFiltered   = 0;
float spo2Filtered = 0;
bool  hrReady   = false;
bool  spo2Ready = false;

float hrRaw   = 0;
float spo2Raw = 0;

int bpm  = 0;
int spo2 = 0;
bool readingValid = false;

int lastValidBpm  = 0;
int lastValidSpo2 = 0;

bool hrHighAlarmActive  = false;
bool hrLowAlarmActive   = false;
bool spo2LowAlarmActive = false;

// ==================== Temporizzazioni ====================
const unsigned long DISPLAY_UPDATE_INTERVAL_MS = 250;
const unsigned long FSM_UPDATE_INTERVAL_MS     = 250;

unsigned long lastDisplayUpdateMs = 0;
unsigned long lastFsmUpdateMs     = 0;

// ==================== Pulsante ====================
const unsigned long DEBOUNCE_MS              = 50;
const unsigned long LONG_PRESS_MS            = 2000;
const unsigned long RESET_SEQUENCE_WINDOW_MS = 5000;

bool lastRawButtonLevel = HIGH;
bool buttonPressed      = false;
unsigned long lastLevelChangeMs = 0;
unsigned long pressStartMs      = 0;
bool longPressCounted    = false;
int  pendingResetPresses = 0;
unsigned long lastLongPressEndMs = 0;

// ==================== Comunicazione MQTT ====================

// Restituisce il nome testuale dello stato corrente inviato nei log e nella telemetria.
const char* getStateName() {
  switch (currentState) {
    case STATE_MISSION_NOT_STARTED: return "MISSIONE_NON_AVVIATA";
    case STATE_SEARCHING_SIGNAL:    return "RICERCA_SEGNALE";
    case STATE_VERIFYING:           return "VERIFICA";
    case STATE_ALARM:               return "ALLARME";
    case STATE_SILENCED:            return "SILENZIATO";
    case STATE_FAULT:               return "GUASTO";
    default:                        return "NORMALE";
  }
}

// Formatta un valore floating point oppure ``null`` quando non disponibile.
void formatValue(char* dest, size_t size, bool present, float value) {
  if (present) {
    snprintf(dest, size, "%.1f", value);
  } else {
    snprintf(dest, size, "null");
  }
}

// Formatta un valore intero oppure ``null`` quando non disponibile.
void formatInteger(char* dest, size_t size, bool present, int value) {
  if (present) {
    snprintf(dest, size, "%d", value);
  } else {
    snprintf(dest, size, "null");
  }
}

// Pubblica periodicamente valori biometrici, stato FSM e dati grezzi/filtrati.
void publishTelemetry() {
  char hrRawText[12], hrFilteredText[12], spo2RawText[12], spo2FilteredText[12];
  bool recentBeat = (millis() - lastBeatMs <= HR_STALE_TIMEOUT_MS);
  formatValue(hrRawText,        sizeof(hrRawText),        hrRaw > 0 && recentBeat,   hrRaw);
  formatValue(hrFilteredText,   sizeof(hrFilteredText),   hrReady,                   hrFiltered);
  formatValue(spo2RawText,      sizeof(spo2RawText),      spo2Raw > 0 && recentBeat, spo2Raw);
  formatValue(spo2FilteredText, sizeof(spo2FilteredText), spo2Ready,                 spo2Filtered);

  char rawFilteredFields[112];
  snprintf(rawFilteredFields, sizeof(rawFilteredFields),
           "\"hr_grezzo\":%s,\"hr_filtrato\":%s,\"spo2_grezzo\":%s,\"spo2_filtrato\":%s",
           hrRawText, hrFilteredText, spo2RawText, spo2FilteredText);

  char bpmText[8], spo2Text[8];
  formatInteger(bpmText,  sizeof(bpmText),  hrReady,   bpm);
  formatInteger(spo2Text, sizeof(spo2Text), spo2Ready, spo2);

  char payload[224];
  snprintf(payload, sizeof(payload),
           "{\"bpm\":%s,\"spo2\":%s,\"stato\":\"%s\",\"lettura_valida\":%s,%s}",
           bpmText, spo2Text, getStateName(), readingValid ? "true" : "false", rawFilteredFields);
  mqtt.publish(TELEMETRY_TOPIC, payload);
}

// Memorizza un nuovo allarme biometrico in attesa di consegna al drone.
void publishBiometricAlarm() {
  formatInteger(pendingAlarmBpm,  sizeof(pendingAlarmBpm),  hrReady,   bpm);
  formatInteger(pendingAlarmSpo2, sizeof(pendingAlarmSpo2), spo2Ready, spo2);
  biometricAlarmPending = true;
  Serial.print("[ALLARME] valori fuori soglia, BPM ");
  Serial.print(vitalText(hrReady, bpm));
  Serial.print(" e SpO2 ");
  Serial.println(spo2Ready ? String(spo2) + "%" : String("--"));
}

// Invia l’allarme biometrico pendente quando il broker è disponibile.
void sendPendingBiometricAlarm() {
  if (!biometricAlarmPending) return;
  char payload[160];
  snprintf(payload, sizeof(payload),
           "{\"bpm\":%s,\"spo2\":%s,\"evento\":\"BIOMETRIA_ANOMALA\"}",
           pendingAlarmBpm, pendingAlarmSpo2);
  if (mqtt.publish(TELEMETRY_TOPIC, payload)) {
    biometricAlarmPending = false;
    Serial.println("[ALLARME] avviso inviato al drone");
  }
}

// Estrae un campo stringa dai semplici messaggi JSON usati dal progetto.
String extractField(const String& msg, const char* fieldName) {
  String key = String("\"") + fieldName + "\"";
  int fieldPos = msg.indexOf(key);
  if (fieldPos < 0) return "";
  int i = msg.indexOf(':', fieldPos);
  if (i < 0) return "";
  i++;
  while (i < (int)msg.length() && msg[i] == ' ') i++;
  if (i < (int)msg.length() && msg[i] == '"') {
    int closingQuotePos = msg.indexOf('"', i + 1);
    if (closingQuotePos > i) return msg.substring(i + 1, closingQuotePos);
  }
  return "";
}

// Gestisce i messaggi MQTT ricevuti dal drone: stato della missione,
// segnalazioni sui DPI e accesso ad aree vietate.
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  String msg;
  msg.reserve(length);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];

  String type = extractField(msg, "tipo");

  if (type.length() == 0) {
    Serial.println("[DRONE] allarme non destinato all'orologio, ignorato");
    return;
  }
  String detail = extractField(msg, "dettaglio");
  Serial.print("[DRONE] ");
  Serial.print(type);
  if (detail.length() > 0) {
    Serial.print(" (");
    Serial.print(detail);
    Serial.print(")");
  }
  Serial.println();

  if (type == "AVVIO_MISSIONE") {
    prefs.putBool("missione", true);
    if (!missionActive) {
      missionActive = true;
      sensorTestActive = false;
      if (currentState == STATE_MISSION_NOT_STARTED) {
        currentState = STATE_SEARCHING_SIGNAL;
      }
      showNotice("Missione", "avviata");
      pulseVibration(150);
    }
    return;
  }
  if (type == "FINE_MISSIONE") {

    if (BYPASS_MISSION_WAIT) {
      Serial.println("[DRONE] ignorato: test da banco");
      return;
    }
    bool missionWasRunning = missionActive;
    missionActive = false;
    prefs.putBool("missione", false);
    biometricCauseActive = false;
    hrHighAlarmActive = false;
    hrLowAlarmActive = false;
    spo2LowAlarmActive = false;
    ppeCauseActive = false;
    missingPpeList = "";
    restrictedAreaCauseActive = false;
    currentState = sensorPresent ? STATE_MISSION_NOT_STARTED : STATE_FAULT;
    if (missionWasRunning) {
      showNotice("Missione", "terminata");
      pulseVibration(150);
    }
    return;
  }

  if (!missionActive) {
    Serial.println("[DRONE] ignorato: la missione non e' avviata");
    return;
  }

  if (type == "DPI_MANCANTE") {
    bool isNewCause = !ppeCauseActive;
    ppeCauseActive = true;
    missingPpeList = detail;
    if (isNewCause) currentState = STATE_ALARM;
    return;
  }
  if (type == "AREA_VIETATA") {
    bool isNewCause = !restrictedAreaCauseActive;
    restrictedAreaCauseActive = true;
    if (isNewCause) currentState = STATE_ALARM;
    return;
  }
  if (type == "AREA_OK") { restrictedAreaCauseActive = false; return; }
  if (type == "DPI_OK") { ppeCauseActive = false; missingPpeList = ""; return; }

  Serial.println("[DRONE] tipo sconosciuto, ignorato");
}

// Esegue il collegamento MQTT in un task separato per non bloccare il loop.
void brokerConnectTask(void*) {
  brokerConnectOk = mqtt.connect(MQTT_CLIENT_ID, NULL, NULL,
                                 PRESENCE_TOPIC, 1, true, "offline", false);
  brokerConnectFinished = true;
  vTaskDelete(NULL);
}

// Raccoglie l’esito del task MQTT e completa l’inizializzazione della sessione.
void finishBrokerConnect() {
  if (!brokerConnectRunning || !brokerConnectFinished) return;
  brokerConnectRunning = false;
  if (brokerConnectOk) {
    mqtt.publish(PRESENCE_TOPIC, "online", true);
    mqtt.subscribe(ALARM_TOPIC, 1);
    brokerWritableSinceMs = millis();
    Serial.println("[RETE] broker collegato, in ascolto dei messaggi del drone");
  } else {
    Serial.print("[RETE] Connessione ");
    Serial.print(describeBrokerStatus());
    Serial.print(" (codice ");
    Serial.print(mqtt.state());
    Serial.println(")");
  }
}

// Controlla senza attesa se il socket MQTT può accettare nuovi dati.
bool isBrokerWritable() {
  int fd = wifiClient.fd();
  if (fd < 0) return false;
  fd_set writeSet;
  FD_ZERO(&writeSet);
  FD_SET(fd, &writeSet);
  struct timeval noWait = {0, 0};
  return select(fd + 1, NULL, &writeSet, NULL, &noWait) > 0;
}

// Avvia o riprende in modo non bloccante le connessioni Wi-Fi e MQTT.
void ensureNetwork() {
  if (WiFi.status() != WL_CONNECTED) {

    if (wifiAttemptStarted && millis() - wifiAttemptStartMs < WIFI_CONNECT_TIMEOUT_MS) {
      Serial.print("[RETE] Connessione ");
      Serial.print(describeWifiStatus());
      Serial.print(" (stato ");
      Serial.print(WiFi.status());
      Serial.println(")");
      return;
    }
    Serial.print("[RETE] collego il WiFi ");
    Serial.println(WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    wifiAttemptStartMs = millis();
    wifiAttemptStarted = true;
    return;
  }
  if (!mqtt.connected()) {
    Serial.print("[RETE] WiFi collegato, IP orologio ");
    Serial.print(WiFi.localIP());
    Serial.print(". Collego il broker ");
    Serial.println(MQTT_BROKER_IP);
    brokerConnectFinished = false;
    brokerConnectRunning  = true;

    if (xTaskCreatePinnedToCore(brokerConnectTask, "broker", 6144, NULL, 1, NULL, 0) != pdPASS) {
      brokerConnectRunning = false;
      Serial.println("[RETE] collegamento al broker non avviato: memoria insufficiente");
    }
  }
}

// ==================== Inizializzazione ====================

// Configura il MAX30100 dopo un aggancio riuscito.
void configureSensor() {
  pox.setIRLedCurrent(IR_LED_CURRENT);
  pox.setOnBeatDetectedCallback(onBeatDetected);
  sensorPresent = true;
}

// Inizializza rete, periferiche, sensore, persistenza e stato iniziale.
void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("[AVVIO] Orologio Work Guardian");

  WiFi.mode(WIFI_STA);

  WiFi.setMinSecurity(WIFI_AUTH_WPA_PSK);
  mqtt.setServer(MQTT_BROKER_IP, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setSocketTimeout(2);

  mqtt.setBufferSize(512);

  pinMode(PIN_VIBRATION, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_LED_BLUE, OUTPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);

  lcd.begin(16, 2);
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Work Guardian");
  delay(1500);
  lcd.clear();

  digitalWrite(PIN_VIBRATION, LOW);
  digitalWrite(PIN_LED_BLUE, LOW);
  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_RED, LOW);

  int attempts = 0;
  while (attempts < SENSOR_MAX_ATTEMPTS) {
    if (pox.begin()) {
      configureSensor();
      break;
    }
    attempts++;
    lcd.setCursor(0, 0);
    lcd.print("Sensore assente");
    lcd.setCursor(0, 1);
    lcd.print("Tentativo:");
    lcd.print(attempts);
    lcd.print("/");
    lcd.print(SENSOR_MAX_ATTEMPTS);
    Serial.print("[SENSORE] non risponde, tentativo ");
    Serial.print(attempts);
    Serial.print("/");
    Serial.println(SENSOR_MAX_ATTEMPTS);
    digitalWrite(PIN_LED_RED, HIGH);
    digitalWrite(PIN_LED_GREEN, LOW);
    delay(2000);
  }

  lcd.clear();

  prefs.begin("orologio", false);
  bool missionWasActive = prefs.getBool("missione", false);
  missionActive = BYPASS_MISSION_WAIT || missionWasActive;

  if (!sensorPresent) {
    currentState = STATE_FAULT;
  } else if (missionActive) {
    currentState = STATE_SEARCHING_SIGNAL;
  } else {
    currentState = STATE_MISSION_NOT_STARTED;
  }

  if (sensorPresent) {
    Serial.println("[SENSORE] pronto");
  } else {
    Serial.print("[SENSORE] assente, riprovo ogni ");
    Serial.print(SENSOR_RETRY_INTERVAL_MS / 1000);
    Serial.println(" s");
  }
  Serial.print("[STATO] iniziale ");
  Serial.print(getStateName());
  if (BYPASS_MISSION_WAIT) {
    Serial.println(" (test da banco: attesa missione saltata)");
  } else if (missionWasActive) {
    Serial.println(" (missione ripresa dopo un riavvio)");
  } else {
    Serial.println();
  }
  loggedState = currentState;
  loggedStateName = getStateName();

  unsigned long nowMs = millis();
  lastDisplayUpdateMs = lastFsmUpdateMs = nowMs;
  lastBeatMs = nowMs;
  lastSensorRetryMs = lastSensorCheckMs = nowMs;
}

// Esegue acquisizione biometrica, FSM, interfaccia utente e comunicazione MQTT.
void loop() {
  if (sensorPresent) {
    pox.update();
  }

  handleButton();

  checkSensorConnection();
  retrySensorIfMissing();

  unsigned long nowMs = millis();

  if (sensorPresent) {
    if (newBeat) {
      newBeat = false;
      if (beatIntervalMs > BEATDETECTOR_INVALID_READOUT_DELAY) {

        spo2Raw = 0;
        resetSpo2();
      }
      sampleHr();
    }

    if (newSpo2) {
      newSpo2 = false;
      sampleSpo2();
    }

    if (nowMs - lastBeatMs > HR_STALE_TIMEOUT_MS) {
      resetHr();
      resetSpo2();
    }
  }

  if (nowMs - lastFsmUpdateMs >= FSM_UPDATE_INTERVAL_MS) {
    lastFsmUpdateMs = nowMs;
    readingValid = hrReady && spo2Ready;
    if (hrReady)   lastValidBpm  = bpm;
    if (spo2Ready) lastValidSpo2 = spo2;
    updateFsm();
    if (SERIAL_PLOTTER) printPlotterTelemetry();
  }

  if (nowMs - lastDisplayUpdateMs >= DISPLAY_UPDATE_INTERVAL_MS) {
    lastDisplayUpdateMs = nowMs;
    updateLcd();
    updateLeds();
  }

  updateVibration();

  finishBrokerConnect();

  bool brokerConnected = !brokerConnectRunning && mqtt.connected();
  trackNetwork(brokerConnected);
  if (brokerConnected) {
    if (isBrokerWritable()) {
      brokerWritableSinceMs = millis();
      mqtt.loop();
      sendPendingBiometricAlarm();
      if (nowMs - lastPublishMs >= PUBLISH_INTERVAL_MS) {
        lastPublishMs = nowMs;
        publishTelemetry();
      }
    } else if (millis() - brokerWritableSinceMs >= BROKER_STALL_MS) {

      Serial.println("[RETE] il broker non riceve piu' dati, chiudo la connessione");
      wifiClient.stop();
    }
  } else if (!brokerConnectRunning && nowMs - lastNetworkRetryMs >= NETWORK_RETRY_INTERVAL_MS) {
    lastNetworkRetryMs = nowMs;
    ensureNetwork();
  }

  logStateChange();
}

// ==================== Controllo del sensore ====================

// Legge un registro del MAX30100 tramite I2C.
bool readSensorRegister(uint8_t reg, uint8_t& value) {
  Wire.beginTransmission(MAX30100_I2C_ADDRESS);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MAX30100_I2C_ADDRESS, 1) != 1) return false;
  value = Wire.read();
  return true;
}

// Scrive un registro del MAX30100 tramite I2C.
void writeSensorRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MAX30100_I2C_ADDRESS);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

// Verifica che il MAX30100 risponda e mantenga la modalità di misura prevista.
bool isSensorResponding() {
  uint8_t mode;
  if (!readSensorRegister(MAX30100_REG_MODE_CONFIGURATION, mode)) return false;
  return (mode & 0x07) == MAX30100_MODE_SPO2_HR;
}

// Ripristina la FIFO del MAX30100 quando è rimasta piena dopo una pausa del loop.
void recoverStalledFifo() {
  uint8_t lostSamples;
  if (!readSensorRegister(MAX30100_REG_FIFO_OVERFLOW_COUNTER, lostSamples)) return;
  if (lostSamples == 0) return;
  writeSensorRegister(MAX30100_REG_FIFO_WRITE_POINTER, 0);
  writeSensorRegister(MAX30100_REG_FIFO_OVERFLOW_COUNTER, 0);
  writeSensorRegister(MAX30100_REG_FIFO_READ_POINTER, 0);
  Serial.println("[SENSORE] FIFO piena dopo una pausa del loop, lettura ripristinata");
}

// Controlla periodicamente la presenza del sensore e segnala una perdita persistente.
void checkSensorConnection() {
  if (!sensorPresent) return;

  unsigned long nowMs = millis();
  if (nowMs - lastSensorCheckMs < SENSOR_CHECK_INTERVAL_MS) return;
  lastSensorCheckMs = nowMs;

  if (isSensorResponding()) {
    missedSensorChecks = 0;
    recoverStalledFifo();
    return;
  }
  missedSensorChecks++;
  if (missedSensorChecks < SENSOR_MAX_MISSED_CHECKS) return;

  missedSensorChecks = 0;
  sensorPresent = false;
  newBeat = newSpo2 = false;
  resetHr();
  resetSpo2();
  readingValid = false;
  lastSensorRetryMs = nowMs;

  if (currentState == STATE_MISSION_NOT_STARTED) currentState = STATE_FAULT;
  Serial.print("[SENSORE] non risponde piu', riprovo ogni ");
  Serial.print(SENSOR_RETRY_INTERVAL_MS / 1000);
  Serial.println(" s");
}

// Ritenta periodicamente l’aggancio del sensore senza bloccare il dispositivo.
void retrySensorIfMissing() {
  if (sensorPresent) return;

  unsigned long nowMs = millis();
  if (nowMs - lastSensorRetryMs < SENSOR_RETRY_INTERVAL_MS) return;
  lastSensorRetryMs = nowMs;

  if (pox.begin()) {
    configureSensor();
    if (currentState == STATE_FAULT) {

      currentState = missionActive ? STATE_SEARCHING_SIGNAL : STATE_MISSION_NOT_STARTED;
    }
    Serial.println("[SENSORE] ricollegato");
  } else {
    Serial.print("[SENSORE] ancora assente, riprovo tra ");
    Serial.print(SENSOR_RETRY_INTERVAL_MS / 1000);
    Serial.println(" s");
  }
}

// ==================== Filtraggio frequenza cardiaca ====================

// Pipeline HR: campione grezzo -> plausibilita -> mediana -> EMA -> valore usato dalla FSM.
// Azzera lo stato della catena di filtraggio della frequenza cardiaca.
void resetHr() {
  hrReady = false;
  hrBufferCount = 0;
  hrBufferIndex = 0;
  hrFiltered = 0;
  bpm = 0;
  hrRejectedCount = 0;
}

// Elabora un nuovo battito applicando plausibilità, mediana ed EMA.
void sampleHr() {
  hrRaw = 60000.0 / beatIntervalMs;

  if (hrRaw < HR_MIN_PLAUSIBLE || hrRaw > HR_MAX_PLAUSIBLE) return;

  if (hrReady && fabs(hrRaw - hrFiltered) / hrFiltered > HR_MAX_DEVIATION_RATIO) {
    hrRejected[hrRejectedCount++] = hrRaw;
    if (hrRejectedCount >= HR_MAX_REJECTIONS) handleHrRejections();
    return;
  }
  hrRejectedCount = 0;
  pushHrSample(hrRaw);
}

// Riallinea o resetta la catena HR dopo una sequenza di campioni respinti.
void handleHrRejections() {
  float rejectedMedian = computeMedian(hrRejected, HR_MAX_REJECTIONS);
  bool consistent = areHrSamplesConsistent(hrRejected, HR_MAX_REJECTIONS, rejectedMedian);

  resetHr();
  if (!consistent) {
    Serial.println("[SENSORE] battito instabile, riaggancio la lettura");
    return;
  }

  int first = HR_MAX_REJECTIONS > HR_MEDIAN_WINDOW ? HR_MAX_REJECTIONS - HR_MEDIAN_WINDOW : 0;
  for (int i = first; i < HR_MAX_REJECTIONS; i++) pushHrSample(hrRejected[i]);
  Serial.print("[SENSORE] battito cambiato di colpo, lettura riallineata a ");
  Serial.print(bpm);
  Serial.println(" bpm");
}

// Verifica la coerenza di un gruppo di campioni HR rispetto alla loro mediana.
bool areHrSamplesConsistent(const float* samples, int count, float median) {
  for (int i = 0; i < count; i++) {
    if (fabs(samples[i] - median) / median > HR_MAX_DEVIATION_RATIO) return false;
  }
  return true;
}

// Inserisce un campione HR accettato e aggiorna il valore filtrato.
void pushHrSample(float value) {

  hrBuffer[hrBufferIndex] = value;
  hrBufferIndex = (hrBufferIndex + 1) % HR_MEDIAN_WINDOW;
  if (hrBufferCount < HR_MEDIAN_WINDOW) hrBufferCount++;

  if (hrBufferCount < 3) return;

  int medianCount = (hrBufferCount % 2 == 0) ? hrBufferCount - 1 : hrBufferCount;

  float* medianWindow = hrBuffer + (hrBufferCount - medianCount);
  float hrMedian = computeMedian(medianWindow, medianCount);

  if (!hrReady) {

    if (hrBufferCount < HR_MEDIAN_WINDOW && !areHrSamplesConsistent(medianWindow, medianCount, hrMedian)) return;
    hrFiltered = hrMedian;
    hrReady = true;
  } else {
    hrFiltered = HR_EMA_ALPHA * hrMedian + (1.0 - HR_EMA_ALPHA) * hrFiltered;
  }

  bpm = (int)(hrFiltered + 0.5);
}

// ==================== Filtraggio SpO2 ====================

// Pipeline SpO2: campione grezzo -> plausibilita -> mediana -> EMA -> valore usato dalla FSM.
// Azzera lo stato della catena di filtraggio della SpO2.
void resetSpo2() {
  spo2Ready = false;
  spo2BufferCount = 0;
  spo2BufferIndex = 0;
  spo2Filtered = 0;
  spo2 = 0;
  consecutiveSpo2OutOfRange = 0;
}

// Elabora una nuova SpO2 applicando controllo di plausibilità, mediana ed EMA.
void sampleSpo2() {
  spo2Raw = pox.getSpO2();

  if (spo2Raw < 70 || spo2Raw > 100) {

    consecutiveSpo2OutOfRange++;
    if (consecutiveSpo2OutOfRange >= SPO2_MAX_OUT_OF_RANGE) {
      resetSpo2();
    }
    return;
  }
  consecutiveSpo2OutOfRange = 0;

  spo2Buffer[spo2BufferIndex] = spo2Raw;
  spo2BufferIndex = (spo2BufferIndex + 1) % SPO2_MEDIAN_WINDOW;
  if (spo2BufferCount < SPO2_MEDIAN_WINDOW) spo2BufferCount++;

  if (spo2BufferCount < 2) return;

  float spo2Median;
  if (spo2BufferCount == 2) {

    if (fabs(spo2Buffer[0] - spo2Buffer[1]) > SPO2_LOCK_TOLERANCE) return;
    spo2Median = (spo2Buffer[0] + spo2Buffer[1]) / 2;
  } else {
    int medianCount = (spo2BufferCount % 2 == 0) ? spo2BufferCount - 1 : spo2BufferCount;
    spo2Median = computeMedian(spo2Buffer + (spo2BufferCount - medianCount), medianCount);
  }

  if (!spo2Ready) {
    spo2Filtered = spo2Median;
    spo2Ready = true;
  } else {
    spo2Filtered = SPO2_EMA_ALPHA * spo2Median + (1.0 - SPO2_EMA_ALPHA) * spo2Filtered;
  }

  spo2 = (int)(spo2Filtered + 0.5);
}

// Calcola la mediana di un piccolo insieme di campioni.
float computeMedian(float* buf, int n) {
  float tmp[8];
  for (int i = 0; i < n; i++) tmp[i] = buf[i];
  for (int i = 1; i < n; i++) {
    float key = tmp[i];
    int j = i - 1;
    while (j >= 0 && tmp[j] > key) {
      tmp[j + 1] = tmp[j];
      j--;
    }
    tmp[j + 1] = key;
  }
  return tmp[n / 2];
}

// ==================== Logica della FSM ====================
bool isHrAboveEntry()   { return hrReady && bpm > BPM_MAX_IN; }
bool isHrBelowEntry()   { return hrReady && bpm < BPM_MIN_IN; }
bool isSpo2BelowEntry() { return spo2Ready && spo2 < SPO2_MIN_IN; }

// Indica se almeno un parametro supera una soglia di ingresso dell’allarme.
bool isOutsideEntryThresholds() {
  return isHrAboveEntry() || isHrBelowEntry() || isSpo2BelowEntry();
}

bool isHrBackFromHigh()  { return hrReady && bpm <= BPM_MAX_OUT; }
bool isHrBackFromLow()   { return hrReady && bpm >= BPM_MIN_OUT; }
bool isSpo2BackFromLow() { return spo2Ready && spo2 >= SPO2_MIN_OUT; }

// Conferma la causa biometrica dopo la persistenza prevista.
void confirmBiometricAlarm() {
  biometricCauseActive = true;
  hrHighAlarmActive  = isHrAboveEntry();
  hrLowAlarmActive   = isHrBelowEntry();
  spo2LowAlarmActive = isSpo2BelowEntry();
  currentState = STATE_ALARM;
  publishBiometricAlarm();
}

// Aggiorna la macchina a stati e gestisce ingresso, silenziamento e risoluzione degli allarmi.
// Le soglie di ingresso attivano la verifica dell'anomalia; le soglie di uscita,
// separate da quelle di ingresso, introducono isteresi e impediscono transizioni
// ripetute quando una misura oscilla vicino al limite.
void updateFsm() {

  if (!missionActive) return;

  if (biometricCauseActive) {
    if (isHrAboveEntry())   hrHighAlarmActive = true;
    if (isHrBelowEntry())   hrLowAlarmActive = true;
    if (isSpo2BelowEntry()) spo2LowAlarmActive = true;
    if (hrHighAlarmActive && isHrBackFromHigh())   hrHighAlarmActive = false;
    if (hrLowAlarmActive && isHrBackFromLow())     hrLowAlarmActive = false;
    if (spo2LowAlarmActive && isSpo2BackFromLow()) spo2LowAlarmActive = false;
    biometricCauseActive = hrHighAlarmActive || hrLowAlarmActive || spo2LowAlarmActive;
  }

  if ((currentState == STATE_ALARM || currentState == STATE_SILENCED)
      && !biometricCauseActive && isOutsideEntryThresholds()) {
    if (!maskedCriticalPending) {
      maskedCriticalPending = true;
      maskedCriticalStartMs = millis();
    } else if (millis() - maskedCriticalStartMs >= PERSISTENCE_MS) {
      maskedCriticalPending = false;
      confirmBiometricAlarm();
    }
  } else {
    maskedCriticalPending = false;
  }

  if (isAnyCauseActive()) {

    if (currentState != STATE_SILENCED) currentState = STATE_ALARM;
  } else {

    if (currentState == STATE_ALARM || currentState == STATE_SILENCED) {
      currentState = STATE_NORMAL;
    }
    if (!sensorPresent) {
      currentState = STATE_FAULT;
    } else if (!readingValid && !isOutsideEntryThresholds()) {

      currentState = STATE_SEARCHING_SIGNAL;
    }
  }

  switch (currentState) {

    case STATE_SEARCHING_SIGNAL:
      if (isOutsideEntryThresholds()) {
        currentState = STATE_VERIFYING;
        verifyStartMs = millis();
      } else if (readingValid) {
        currentState = STATE_NORMAL;
      }
      break;

    case STATE_NORMAL:
      if (isOutsideEntryThresholds()) {
        currentState = STATE_VERIFYING;
        verifyStartMs = millis();
      }
      break;

    case STATE_VERIFYING:
      if (!isOutsideEntryThresholds()) {
        currentState = STATE_NORMAL;
      } else if (millis() - verifyStartMs >= PERSISTENCE_MS) {
        confirmBiometricAlarm();
      }
      break;

    case STATE_ALARM:

      break;

    case STATE_SILENCED:
      if (millis() - silenceStartMs >= SILENCE_TIMEOUT_MS) {
        currentState = STATE_ALARM;
      }
      break;

    case STATE_FAULT:
      if (sensorPresent) {
        currentState = STATE_SEARCHING_SIGNAL;
      }
      break;

    default:
      break;
  }
}

// Stampa i valori destinati al Plotter seriale.
void printPlotterTelemetry() {
  Serial.print("HR_grezzo:");
  Serial.print(hrRaw);
  Serial.print(",HR_filtrato:");
  Serial.print(hrFiltered);
  Serial.print(",SpO2_grezzo:");
  Serial.print(spo2Raw);
  Serial.print(",SpO2_filtrato:");
  Serial.println(spo2Filtered);
}

// Registra sul monitor seriale ogni cambio di stato della FSM.
void logStateChange() {
  if (currentState == loggedState) return;
  Serial.print("[STATO] ");
  Serial.print(loggedStateName);
  Serial.print(" -> ");
  Serial.println(getStateName());
  loggedState = currentState;
  loggedStateName = getStateName();
}

// ==================== Comandi dell’operatore ====================

// Gestisce debounce, pressione breve, pressione lunga e sequenza di reset.
void handleButton() {
  bool level = digitalRead(PIN_BUTTON);

  if (level != lastRawButtonLevel) {
    lastRawButtonLevel = level;
    lastLevelChangeMs = millis();
  }

  if (millis() - lastLevelChangeMs >= DEBOUNCE_MS) {
    bool pressedNow = (level == LOW);

    if (pressedNow && !buttonPressed) {
      buttonPressed = true;
      pressStartMs = millis();
      longPressCounted = false;
    }
    else if (!pressedNow && buttonPressed) {
      buttonPressed = false;

      if (longPressCounted) {

        lastLongPressEndMs = millis();
      } else {

        pendingResetPresses = 0;
        if (currentState == STATE_ALARM) {
          currentState = STATE_SILENCED;
          silenceStartMs = millis();
          Serial.print("[PULSANTE] pressione breve: allarme silenziato per ");
          Serial.print(SILENCE_TIMEOUT_MS / 1000);
          Serial.println(" s");
          updateLeds();
          updateLcd();
        } else if (!missionActive) {
          toggleSensorTest();
        } else {
          Serial.println("[PULSANTE] pressione breve: nessun allarme da silenziare");
        }
      }
    }
  }

  if (buttonPressed && !longPressCounted &&
      (millis() - pressStartMs >= LONG_PRESS_MS)) {
    longPressCounted = true;

    if (pendingResetPresses == 0) {
      pendingResetPresses = 1;
      Serial.print("[PULSANTE] pressione lunga 1 di 2: ripeti entro ");
      Serial.print(RESET_SEQUENCE_WINDOW_MS / 1000);
      Serial.println(" s per riavviare");
      pulseVibration(150);
    } else {
      Serial.println("[PULSANTE] pressione lunga 2 di 2: riavvio del dispositivo");
      restartDevice();
    }
  }

  if (pendingResetPresses == 1 && !buttonPressed &&
      (millis() - lastLongPressEndMs > RESET_SEQUENCE_WINDOW_MS)) {
    pendingResetPresses = 0;
    Serial.println("[PULSANTE] riavvio annullato: la seconda pressione lunga non e' arrivata");
  }
}

// Attiva o disattiva la prova biometrica fuori missione.
void toggleSensorTest() {
  if (!sensorTestActive && !sensorPresent) {

    Serial.println("[PULSANTE] pressione breve: prova sensore non disponibile, sensore assente");
    return;
  }

  sensorTestActive = !sensorTestActive;
  Serial.print("[PULSANTE] pressione breve: prova sensore ");
  Serial.println(sensorTestActive ? "attivata" : "disattivata");

  if (sensorTestActive) {
    showNotice("Sensore biometr.", "attivo");
  } else {
    showNotice("Prova sensore", "terminata");
  }
}

// Avvia un breve impulso tattile non bloccante.
void pulseVibration(unsigned long durationMs) {
  feedbackPulseStartMs = millis();
  feedbackPulseMs = durationMs;
  digitalWrite(PIN_VIBRATION, HIGH);
}

// Salva lo stato necessario e riavvia il Nano ESP32.
void restartDevice() {
  Serial.flush();

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Reset in corso");
  digitalWrite(PIN_LED_RED, HIGH);
  digitalWrite(PIN_LED_GREEN, LOW);
  digitalWrite(PIN_LED_BLUE, LOW);

  digitalWrite(PIN_VIBRATION, HIGH);
  delay(400);
  digitalWrite(PIN_VIBRATION, LOW);

  ESP.restart();
}

// ==================== Display, rete e segnalazioni ====================

// Mostra un avviso temporaneo ad alta priorità sull’LCD.
void showNotice(const char* line1, const char* line2) {
  noticeLine1 = line1;
  noticeLine2 = line2;
  noticeStartMs = millis();
  Serial.print("[AVVISO] ");
  Serial.print(line1);
  Serial.print(" ");
  Serial.println(line2);
  updateLcd();
  updateLeds();
}

// Indica se un avviso temporaneo è ancora attivo.
bool isNoticeShown() {
  return noticeLine1 != nullptr
      && currentState != STATE_ALARM && currentState != STATE_SILENCED
      && millis() - noticeStartMs < NOTICE_MS;
}

// Indica se deve essere visualizzata una perdita persistente della rete.
bool isNetworkLossShown() {
  return brokerEverConnected && networkReportedDown;
}

// Aggiorna lo stato della connessione e registra cadute o ripristini del broker.
void trackNetwork(bool brokerConnected) {
  if (brokerConnected) {
    brokerWasConnected = true;
    brokerEverConnected = true;
    networkReportedDown = false;
    return;
  }
  if (brokerWasConnected) {
    brokerWasConnected = false;
    brokerLostSinceMs = millis();
  }
  if (!networkReportedDown && millis() - brokerLostSinceMs >= NETWORK_LOSS_GRACE_MS) {
    networkReportedDown = true;
    Serial.print("[RETE] connessione persa da ");
    Serial.print(NETWORK_LOSS_GRACE_MS / 1000);
    Serial.println(" s");
  }
}

// Restituisce una descrizione sintetica dello stato Wi-Fi.
const char* describeWifiStatus() {
  wl_status_t wifi = WiFi.status();
  if (wifi == WL_NO_SSID_AVAIL)  return "assente";
  if (wifi == WL_CONNECT_FAILED) return "Password errata";
  return "in corso...";
}

// Restituisce una descrizione sintetica dello stato MQTT.
const char* describeBrokerStatus() {
  int broker = mqtt.state();
  if (broker == MQTT_CONNECT_FAILED || broker == MQTT_CONNECTION_TIMEOUT) {
    return "Broker assente";
  }
  return "al Broker...";
}

// Costruisce il testo LCD relativo allo stato della rete.
const char* buildNetworkStatusText() {

  if (!networkReportedDown) return "stabilita";
  if (WiFi.status() != WL_CONNECTED) return describeWifiStatus();
  return describeBrokerStatus();
}

// Costruisce e visualizza le due righe dell’LCD in base a stato e priorità correnti.
void updateLcd() {

  int cause = selectCauseToDisplay();
  if (cause != displayedCause) {
    displayedCause = cause;
    displayedCauseSinceMs = millis();
  }

  bool faultTurn = currentState == STATE_FAULT && (millis() / CAUSE_ROTATION_MS) % 2 == 1;
  String line1, line2;
  if (currentState == STATE_ALARM) {
    buildAlarmLines(cause, line1, line2);
  } else if (currentState == STATE_SILENCED) {
    buildAlarmLines(cause, line1, line2);

    unsigned long silencedMs = millis() - silenceStartMs;
    unsigned long remainingSec = silencedMs < SILENCE_TIMEOUT_MS ? (SILENCE_TIMEOUT_MS - silencedMs) / 1000 : 0;
    line2 = String("Silenziato:") + remainingSec + "s";
  } else if (isNoticeShown()) {
    line1 = noticeLine1;
    line2 = noticeLine2;
  } else if (isNetworkLossShown() && !faultTurn && !sensorTestActive) {

    line1 = "Connessione";
    line2 = "persa";
  } else {
    switch (currentState) {
      case STATE_MISSION_NOT_STARTED:
        if (sensorTestActive) {
          buildSensorTestLines(line1, line2);
        } else {
          line1 = "Connessione";
          line2 = buildNetworkStatusText();
        }
        break;
      case STATE_SEARCHING_SIGNAL:
        line1 = "Appoggia il dito";
        line2 = "sul sensore";
        break;
      case STATE_FAULT:
        line1 = "Sensore biometr.";
        line2 = "irraggiungibile";
        break;
      case STATE_VERIFYING:
        line1 = buildVitalsText();
        line2 = "Controllo valori";
        break;
      default:
        line1 = buildVitalsText();
        line2 = "Valori normali";
        break;
    }
  }

  unsigned long heldMs = buttonPressed ? (millis() - pressStartMs) : 0;

  if (buttonPressed && heldMs >= 400 && !longPressCounted) {

    int remainingSec = (LONG_PRESS_MS - heldMs + 999) / 1000;
    line1 = "Reset orologio";
    line2 = String("Tieni premuto:") + remainingSec + "s";
  } else if (pendingResetPresses == 1) {
    line1 = "Reset orologio";
    line2 = "Ripeti per reset";
  }

  printLcdLine(0, line1);
  printLcdLine(1, line2);
}

// Scrive una riga LCD completa, tagliando e riempiendo fino a 16 caratteri.
void printLcdLine(int row, const String& text) {
  String line = text;
  if (line.length() > 16) line = line.substring(0, 16);
  while (line.length() < 16) line += ' ';
  lcd.setCursor(0, row);
  lcd.print(line);
}

// Conta i DPI mancanti presenti nell'elenco ricevuto dal drone.
int countMissingPpe() {
  int count = 1;
  for (unsigned int i = 0; i < missingPpeList.length(); i++) {
    if (missingPpeList[i] == ',') count++;
  }
  return count;
}

// Costruisce le righe LCD associate a un allarme biometrico.
void buildBiometricLines(String& line1, String& line2) {
  int bpm  = lastValidBpm;
  int spo2 = lastValidSpo2;
  bool hrAlarm = hrHighAlarmActive || hrLowAlarmActive;

  if (hrAlarm && !spo2LowAlarmActive) {
    line1 = hrHighAlarmActive ? "Battito alto:" : "Battito basso:";
    line2 = String(bpm) + " bpm";
  } else if (spo2LowAlarmActive && !hrAlarm) {
    line1 = "SpO2 bassa:";
    line2 = String(spo2) + " %";
  } else {
    line1 = String("Battito: ") + bpm + " bpm";
    line2 = String("SpO2: ") + spo2 + " %";
  }
}

// Formatta un valore vitale oppure ``--`` se non ancora disponibile.
String vitalText(bool ready, int value) {
  return ready ? String(value) : String("--");
}

// Costruisce la riga LCD con BPM e SpO2.
String buildVitalsText() {
  String spo2Text = String("SpO2:") + (spo2Ready ? String(spo2) + "%" : String("--"));
  String line = String("BPM:") + vitalText(hrReady, bpm) + " ";
  while (line.length() + spo2Text.length() < LCD_COLUMNS) line += ' ';
  return line + spo2Text;
}

// Costruisce la schermata della prova sensore fuori missione.
void buildSensorTestLines(String& line1, String& line2) {
  if (readingValid) {
    line1 = buildVitalsText();
    line2 = "Prova sensore";
  } else {
    line1 = "Appoggia il dito";
    line2 = "sul sensore";
  }
}

// Restituisce la causa attiva con priorità maggiore.
int getMostSevereCause() {
  if (biometricCauseActive)      return CAUSE_BIOMETRIC;
  if (restrictedAreaCauseActive) return CAUSE_RESTRICTED_AREA;
  if (ppeCauseActive)            return CAUSE_PPE;
  return CAUSE_NONE;
}

// Seleziona la causa da mostrare quando più allarmi sono contemporaneamente attivi.
int selectCauseToDisplay() {
  int activeCauses[3];
  int activeCauseCount = 0;
  if (biometricCauseActive)      activeCauses[activeCauseCount++] = CAUSE_BIOMETRIC;
  if (restrictedAreaCauseActive) activeCauses[activeCauseCount++] = CAUSE_RESTRICTED_AREA;
  if (ppeCauseActive)            activeCauses[activeCauseCount++] = CAUSE_PPE;

  int activeMask = 0;
  for (int i = 0; i < activeCauseCount; i++) activeMask |= (1 << activeCauses[i]);
  int newMask = activeMask & ~prevCauseMask;
  prevCauseMask = activeMask;

  if (activeCauseCount == 0) return CAUSE_NONE;
  if (newMask) {
    for (int i = 0; i < activeCauseCount; i++) {
      if (newMask & (1 << activeCauses[i])) return activeCauses[i];
    }
  }
  if (activeCauseCount == 1) return activeCauses[0];

  int displayedIndex = 0;
  bool displayedStillActive = false;
  for (int i = 0; i < activeCauseCount; i++) {
    if (activeCauses[i] == displayedCause) {
      displayedIndex = i;
      displayedStillActive = true;
    }
  }
  if (!displayedStillActive) return activeCauses[0];
  if (millis() - displayedCauseSinceMs < CAUSE_ROTATION_MS) return displayedCause;
  return activeCauses[(displayedIndex + 1) % activeCauseCount];
}

// Genera la finestra scorrevole per testi più lunghi di 16 caratteri.
String getScrollWindow(const String& text) {
  if (text.length() <= LCD_COLUMNS) return text;
  unsigned long elapsedMs = millis() - displayedCauseSinceMs;
  if (elapsedMs < SCROLL_START_PAUSE_MS) return text.substring(0, LCD_COLUMNS);

  String ring = text + "   ";
  unsigned int offset = ((elapsedMs - SCROLL_START_PAUSE_MS) / SCROLL_STEP_MS) % ring.length();
  String window = ring.substring(offset);
  while (window.length() < LCD_COLUMNS) window += ring;
  return window.substring(0, LCD_COLUMNS);
}

// Costruisce le righe LCD per la causa di allarme selezionata.
void buildAlarmLines(int cause, String& line1, String& line2) {
  if (cause == CAUSE_BIOMETRIC) {
    buildBiometricLines(line1, line2);
  } else if (cause == CAUSE_RESTRICTED_AREA) {
    line1 = "Area vietata !!";
    line2 = "Allontanarsi";
  } else if (cause == CAUSE_PPE) {
    int count = countMissingPpe();
    if (count > 1) {
      line1 = String(count) + " DPI assenti";
    } else {
      line1 = "DPI assente !!";
    }
    line2 = getScrollWindow(missingPpeList);
  }
}

// Aggiorna il motore secondo il ritmo associato alla causa più grave.
void updateVibration() {
  int cause = (currentState == STATE_ALARM) ? getMostSevereCause() : CAUSE_NONE;
  if (cause != vibrationCause) {
    vibrationCause = cause;
    vibrationCauseSinceMs = millis();
  }

  bool motorOn = false;
  unsigned long elapsedMs = millis() - vibrationCauseSinceMs;
  if (cause == CAUSE_RESTRICTED_AREA) {
    motorOn = (elapsedMs % AREA_VIBRATION_PERIOD_MS) < AREA_VIBRATION_ON_MS;
  } else if (cause == CAUSE_PPE) {
    motorOn = (elapsedMs % PPE_VIBRATION_PERIOD_MS) < PPE_VIBRATION_ON_MS;
  } else if (cause == CAUSE_BIOMETRIC) {
    motorOn = true;
  }
  if (millis() - feedbackPulseStartMs < feedbackPulseMs) motorOn = true;
  digitalWrite(PIN_VIBRATION, motorOn ? HIGH : LOW);
}

// Aggiorna il LED RGB in base allo stato corrente.
void updateLeds() {
  if (isNoticeShown()) {
    digitalWrite(PIN_LED_RED, LOW);
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_BLUE, HIGH);
    return;
  }

  switch (currentState) {
    case STATE_MISSION_NOT_STARTED:
      digitalWrite(PIN_LED_RED, LOW);
      digitalWrite(PIN_LED_BLUE, LOW);
      if (sensorTestActive) {

        digitalWrite(PIN_LED_GREEN, readingValid ? HIGH : (millis() / 500) % 2);
      } else {
        digitalWrite(PIN_LED_GREEN, (millis() / 1000) % 2);
      }
      return;

    case STATE_SEARCHING_SIGNAL:
      digitalWrite(PIN_LED_RED, LOW);
      digitalWrite(PIN_LED_BLUE, LOW);
      digitalWrite(PIN_LED_GREEN, (millis() / 500) % 2);
      break;

    case STATE_ALARM:
      digitalWrite(PIN_LED_RED, HIGH);
      digitalWrite(PIN_LED_GREEN, LOW);
      digitalWrite(PIN_LED_BLUE, LOW);
      break;

    case STATE_SILENCED:
      digitalWrite(PIN_LED_RED, LOW);
      digitalWrite(PIN_LED_GREEN, LOW);
      digitalWrite(PIN_LED_BLUE, HIGH);
      break;

    case STATE_FAULT:
      digitalWrite(PIN_LED_RED, HIGH);
      digitalWrite(PIN_LED_GREEN, LOW);
      digitalWrite(PIN_LED_BLUE, LOW);
      break;

    default:
      digitalWrite(PIN_LED_RED, LOW);
      digitalWrite(PIN_LED_GREEN, HIGH);
      digitalWrite(PIN_LED_BLUE, LOW);
      break;
  }
}
