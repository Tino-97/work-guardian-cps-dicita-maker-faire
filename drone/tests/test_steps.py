from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.flight import steps
from drone.flight.steps import handle_autonomy_step, handle_dpi_step, handle_person_step


class _Registro:
    def __init__(self):
        self.passi: list[str] = []
        self.eventi: list[tuple[str, str, str]] = []
        self.etichette: list[str] = []


class _Controller:
    def __init__(self, registro, *, rompe=frozenset()):
        self._registro = registro
        self._rompe = rompe
        self.is_flying = True
        self.rc = None

    def send_rc_control(self, lr, fb, ud, yaw):
        self._registro.passi.append("rc")
        if "rc" in self._rompe:
            raise RuntimeError("rc")
        self.rc = (lr, fb, ud, yaw)

    def land(self):
        self._registro.passi.append("land")
        if "land" in self._rompe:
            raise RuntimeError("land")
        self.is_flying = False


class _PilotCommands:
    def __init__(self, registro, *, autonomy=True):
        self._registro = registro
        self._autonomy = autonomy

    def is_autonomy_enabled(self):
        return self._autonomy

    def disable_autonomy(self):
        self._autonomy = False
        self._registro.passi.append("disable_autonomy")


class _Autopilot:
    def __init__(self, registro, comando, *, home_index=None, waypoints=4):
        self._registro = registro
        self._comando = comando
        self.home_waypoint_index = home_index
        self.waypoints = tuple(range(waypoints))
        self.pose_ricevuta = "non chiamato"

    def get_current_waypoint(self):
        self._registro.passi.append("get_current_waypoint")
        return "bersaglio"

    def compute_command(self, pose_estimate):
        self._registro.passi.append("compute_command")
        self.pose_ricevuta = pose_estimate
        return self._comando


class _VisionLoop:
    def __init__(self, registro, *, comando=None, detections=None, tag=(), modelli=()):
        self._registro = registro
        self.last_autopilot_command = comando
        self._detections = detections if detections is not None else {}
        self._tag = set(tag)
        self._modelli = set(modelli)
        self.verdetto = None
        self.overlay = None
        self.allarmi_inviati: list[dict] = []

    def get_latest_pose_estimate(self):
        return "posa"

    def publish_alarm(self, *, kind, message, level="warning", tipo=None, dettaglio=None):
        self._registro.passi.append("allarme_sul_canale")
        self.allarmi_inviati.append({
            "kind": kind,
            "message": message,
            "level": level,
            "tipo": tipo,
            "dettaglio": dettaglio,
        })
        return True

    def update_autopilot_overlay(self, command):
        self._registro.passi.append("overlay")
        self.overlay = command

    def clear_autopilot_overlay(self):
        self._registro.passi.append("clear_overlay")

    def get_visible_tag_ids(self):
        return self._tag

    def get_detected_model_names(self):
        return self._modelli

    def get_cached_detections_snapshot(self):
        return self._detections

    def set_safety_net_verdict(self, verdict):
        self._registro.passi.append("banner")
        self.verdetto = verdict


class _Dashboard:
    def __init__(self, registro):
        self._registro = registro
        self.comando = None

    def set_autopilot(self, command):
        self._registro.passi.append("dashboard")
        self.comando = command


class _Logger:
    def __init__(self, registro):
        self._registro = registro
        self.ricevuto = None

    def log_autopilot_step(self, *, pose_estimate, command, target):
        self._registro.passi.append("log")
        self.ricevuto = {"pose_estimate": pose_estimate, "command": command, "target": target}


class _SafetyNetMonitor:
    def __init__(self, registro, verdetto=None, *, nome="collective_protection"):
        self._registro = registro
        self._verdetto = verdetto
        self.safety_net_model_name = nome
        self.ricevuto = None

    def update(self, **kwargs):
        self._registro.passi.append("safety_net")
        self.ricevuto = kwargs
        return self._verdetto


class _Monitor:
    def __init__(self, registro, allarmi=()):
        self._registro = registro
        self._allarmi = list(allarmi)
        self.ricevuto = None

    def update(self, *, detections_by_model, supervision_active):
        self._registro.passi.append("monitor")
        self.ricevuto = {
            "detections_by_model": detections_by_model,
            "supervision_active": supervision_active,
        }
        return self._allarmi


class _Config:
    def __init__(self, auto_land):
        self.apriltag_autopilot = type("_Ap", (), {"auto_land_on_finish": auto_land})()


class _Banco:
    def __init__(self, comando, **over):
        self.registro = _Registro()
        self.controller = _Controller(self.registro, rompe=over.get("rompe", frozenset()))
        self.pilot_commands = _PilotCommands(self.registro, autonomy=over.get("autonomy", True))
        self.autopilot = (
            None
            if over.get("senza_autopilota")
            else _Autopilot(
                self.registro,
                comando,
                home_index=over.get("home_index"),
                waypoints=over.get("waypoints", 4),
            )
        )
        self.vision_loop = _VisionLoop(
            self.registro,
            comando=over.get("ultimo_comando"),
            detections=over.get("detections"),
            tag=over.get("tag", ()),
            modelli=over.get("modelli", ()),
        )
        self.dashboard = _Dashboard(self.registro)
        self.logger = _Logger(self.registro)
        self.safety_net_monitor = over.get("safety_net")


def _con_console(registro, corpo, *, auto_land=True):
    originali = (steps.print_event, steps.log_waypoint_reached, steps.APP_CONFIG)

    def _print_event(msg, *, prefix="EVENTO", channel="drone"):
        registro.eventi.append((str(msg), prefix, channel))

    def _log_waypoint_reached(command, label):
        registro.etichette.append(label)

    steps.print_event = _print_event
    steps.log_waypoint_reached = _log_waypoint_reached
    steps.APP_CONFIG = _Config(auto_land)
    try:
        return corpo()
    finally:
        steps.print_event, steps.log_waypoint_reached, steps.APP_CONFIG = originali


def _esegui_autonomia(banco, *, auto_land=True) -> bool:
    def _corpo():
        return handle_autonomy_step(
            pilot_commands=banco.pilot_commands,
            apriltag_autopilot=banco.autopilot,
            vision_loop=banco.vision_loop,
            dashboard=banco.dashboard,
            flight_data_logger=banco.logger,
            controller=banco.controller,
            safety_net_monitor=banco.safety_net_monitor,
        )

    return _con_console(banco.registro, _corpo, auto_land=auto_land)


def _esegui_sorveglianza(banco, monitor, *, dpi=False) -> None:
    def _corpo():
        gestore = handle_dpi_step if dpi else handle_person_step
        chiave = "dpi_monitor" if dpi else "person_monitor"
        return gestore(
            **{chiave: monitor},
            vision_loop=banco.vision_loop,
            pilot_commands=banco.pilot_commands,
        )

    _con_console(banco.registro, _corpo)


_AVANZA = {"lr": 1, "fb": 2, "ud": 3, "yaw": 4, "reason": "moving", "target_index": 1}


def test_senza_autopilota_l_autonomia_si_disattiva():
    banco = _Banco(None, senza_autopilota=True)

    assert _esegui_autonomia(banco) is True
    assert "disable_autonomy" in banco.registro.passi
    assert "clear_overlay" in banco.registro.passi
    assert banco.registro.eventi[0][1] == "AVVISO"


def test_il_bersaglio_si_legge_prima_di_calcolare_il_comando():
    banco = _Banco(dict(_AVANZA))

    _esegui_autonomia(banco)

    passi = banco.registro.passi
    assert passi.index("get_current_waypoint") < passi.index("compute_command")


def test_i_comandi_rc_dell_autopilota_arrivano_al_drone():
    banco = _Banco(dict(_AVANZA))

    _esegui_autonomia(banco)

    assert banco.controller.rc == (1, 2, 3, 4)


def test_un_comando_senza_assi_manda_zeri():
    banco = _Banco({"reason": "hold"})

    _esegui_autonomia(banco)

    assert banco.controller.rc == (0, 0, 0, 0)


def test_un_errore_nell_invio_rc_non_ferma_il_volo():
    banco = _Banco(dict(_AVANZA), rompe={"rc"})

    assert _esegui_autonomia(banco) is True


def test_la_posa_e_il_bersaglio_finiscono_nel_registro_di_volo():
    banco = _Banco(dict(_AVANZA))

    _esegui_autonomia(banco)

    assert banco.logger.ricevuto["pose_estimate"] == "posa"
    assert banco.logger.ricevuto["target"] == "bersaglio"
    assert banco.autopilot.pose_ricevuta == "posa"


def test_il_comando_arriva_anche_al_cruscotto_e_al_video():
    banco = _Banco(dict(_AVANZA))

    _esegui_autonomia(banco)

    assert banco.dashboard.comando is banco.vision_loop.overlay


def test_il_fault_disinnesca_l_autonomia():
    banco = _Banco({"fault": True, "reason": "pose_timeout"})

    assert _esegui_autonomia(banco) is True
    assert "disable_autonomy" in banco.registro.passi
    messaggio, prefisso, _ = banco.registro.eventi[0]
    assert prefisso == "ERRORE"
    assert messaggio == "Autopilota fermo: posizione persa"


def test_il_fault_di_waypoint_spiega_il_motivo():
    banco = _Banco({"fault": True, "reason": "waypoint_timeout"})

    _esegui_autonomia(banco)

    assert banco.registro.eventi[0][0] == "Autopilota fermo: tempo scaduto"


def test_i_motivi_del_fault_stanno_in_una_riga():
    for motivo in list(steps._FAULT_DETAILS.values()) + ["dati non validi", "guasto"]:
        assert len(motivo) <= len("dati non validi"), motivo


def test_il_fault_di_posa_non_valida_spiega_il_motivo():
    banco = _Banco({"fault": True, "reason": "invalid_pose: matrice singolare"})

    _esegui_autonomia(banco)

    assert banco.registro.eventi[0][0] == "Autopilota fermo: dati non validi"


def test_un_fault_senza_motivo_noto_lo_dice_comunque():
    banco = _Banco({"fault": True, "reason": "qualcosa_di_nuovo"})

    _esegui_autonomia(banco)

    assert banco.registro.eventi[0][0] == "Autopilota fermo: guasto"


def test_il_fault_non_impedisce_l_invio_dei_comandi():
    banco = _Banco({"fault": True, "reason": "pose_timeout", "lr": 5})

    _esegui_autonomia(banco)

    assert banco.controller.rc == (5, 0, 0, 0)


def test_la_fine_missione_atterra_e_chiude_il_volo():
    banco = _Banco({"reached": True, "finished": True})

    assert _esegui_autonomia(banco) is False
    assert "land" in banco.registro.passi
    assert "disable_autonomy" in banco.registro.passi


def test_senza_atterraggio_automatico_la_missione_finita_non_chiude():
    banco = _Banco({"reached": True, "finished": True})

    assert _esegui_autonomia(banco, auto_land=False) is True
    assert "land" not in banco.registro.passi
    assert "disable_autonomy" in banco.registro.passi


def test_un_atterraggio_finale_fallito_chiude_comunque():
    banco = _Banco({"reached": True, "finished": True}, rompe={"land"})

    assert _esegui_autonomia(banco) is False


def test_un_waypoint_raggiunto_non_conclude_la_missione():
    banco = _Banco({"reached": True, "finished": False})

    assert _esegui_autonomia(banco) is True
    assert "land" not in banco.registro.passi


def test_la_sosta_annuncia_la_propria_durata():
    banco = _Banco(
        {
            "reason": "supervision_stop_started",
            "target_index": 1,
            "supervision_stop_remaining_sec": 12.4,
        }
    )

    _esegui_autonomia(banco)

    assert "sosta di 12 s" in banco.registro.eventi[0][0]


def test_l_etichetta_del_waypoint_conta_da_uno():
    banco = _Banco({"reason": "moving", "target_index": 1}, waypoints=4)

    _esegui_autonomia(banco)

    assert banco.registro.etichette == ["2 di 4"]


def test_il_totale_dei_waypoint_non_conta_la_home():
    banco = _Banco({"reason": "moving", "target_index": 1}, home_index=3, waypoints=4)

    _esegui_autonomia(banco)

    assert banco.registro.etichette == ["2 di 3"]


def test_l_etichetta_della_home_non_e_un_numero():
    banco = _Banco({"reason": "moving", "target_index": 3}, home_index=3, waypoints=4)

    _esegui_autonomia(banco)

    assert banco.registro.etichette == ["home"]


def test_l_etichetta_senza_bersaglio():
    banco = _Banco({"reason": "moving"})

    _esegui_autonomia(banco)

    assert banco.registro.etichette == ["--"]


def test_il_verdetto_della_rete_va_nel_log_e_sul_video():
    banco = _Banco(
        dict(_AVANZA),
        safety_net=_SafetyNetMonitor(_Registro(), {"waypoint": 2, "outcome": "present"}),
    )

    _esegui_autonomia(banco)

    messaggio, prefisso, canale = banco.registro.eventi[0]
    assert (prefisso, canale) == ("RETE", "alert")
    assert messaggio == "Rete presente al waypoint 2"
    assert banco.vision_loop.verdetto["outcome"] == "present"


def test_la_rete_mancante_e_un_allerta():
    banco = _Banco(
        dict(_AVANZA),
        safety_net=_SafetyNetMonitor(_Registro(), {"waypoint": 2, "outcome": "missing"}),
    )

    _esegui_autonomia(banco)

    assert banco.registro.eventi[0][1] == "ALLERTA"


def test_la_rete_non_verificata_non_e_un_allerta():
    banco = _Banco(
        dict(_AVANZA),
        safety_net=_SafetyNetMonitor(_Registro(), {"waypoint": 2, "outcome": "no_tags"}),
    )

    _esegui_autonomia(banco)

    assert banco.registro.eventi[0][1] == "RETE"


def test_senza_verdetto_non_si_dice_niente():
    banco = _Banco(dict(_AVANZA), safety_net=_SafetyNetMonitor(_Registro(), None))

    _esegui_autonomia(banco)

    assert banco.registro.eventi == []
    assert banco.vision_loop.verdetto is None


def test_il_monitor_reti_sa_se_il_modello_ha_visto_la_rete():
    monitor = _SafetyNetMonitor(_Registro(), None, nome="collective_protection")
    banco = _Banco(dict(_AVANZA), safety_net=monitor, modelli=("collective_protection",), tag=(7,))

    _esegui_autonomia(banco)

    assert monitor.ricevuto["safety_net_detected"] is True
    assert monitor.ricevuto["visible_tag_ids"] == {7}
    assert monitor.ricevuto["target_index"] == 1


def test_il_monitor_reti_non_confonde_un_altro_modello():
    monitor = _SafetyNetMonitor(_Registro(), None, nome="collective_protection")
    banco = _Banco(dict(_AVANZA), safety_net=monitor, modelli=("restricted_areas",))

    _esegui_autonomia(banco)

    assert monitor.ricevuto["safety_net_detected"] is False


def test_la_sorveglianza_delle_persone_e_attiva_solo_in_sosta():
    banco = _Banco(
        None,
        ultimo_comando={"supervision_stop_active": True},
        detections=[{"name": "person_fall", "detections": []}],
    )
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor)

    assert monitor.ricevuto["supervision_active"] is True


def test_la_sosta_non_e_osservata_prima_dei_primi_risultati():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True}, detections=[])
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor, dpi=True)

    assert monitor.ricevuto["supervision_active"] is False


def test_fuori_dalla_sosta_la_sorveglianza_non_controlla():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": False})
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor)

    assert monitor.ricevuto["supervision_active"] is False


def test_in_manuale_la_sorveglianza_non_e_mai_in_sosta():
    banco = _Banco(None, autonomy=False, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor)

    assert monitor.ricevuto["supervision_active"] is False


def test_senza_comando_dell_autopilota_la_sorveglianza_non_esplode():
    banco = _Banco(None, ultimo_comando=None)
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor)

    assert monitor.ricevuto["supervision_active"] is False


def test_gli_allarmi_delle_persone_finiscono_nel_log_degli_alert():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(banco.registro, [{"message": "Persona a terra"}])

    _esegui_sorveglianza(banco, monitor)

    assert banco.registro.eventi == [("Persona a terra", "AVVISO", "alert")]


def test_un_allarme_senza_messaggio_usa_il_titolo():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(banco.registro, [{"title": "Area interdetta"}])

    _esegui_sorveglianza(banco, monitor)

    assert banco.registro.eventi[0][0] == "Area interdetta"


def test_le_detection_arrivano_al_monitor_delle_persone():
    banco = _Banco(None, ultimo_comando={}, detections={"Cadute": ["box"]})
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor)

    assert monitor.ricevuto["detections_by_model"] == {"Cadute": ["box"]}


def test_senza_monitor_delle_persone_non_succede_niente():
    banco = _Banco(None, ultimo_comando={})

    _esegui_sorveglianza(banco, None)

    assert banco.registro.passi == []


def test_gli_allarmi_dpi_finiscono_nel_log_degli_alert():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(banco.registro, [{"message": "Elmetto assente"}])

    _esegui_sorveglianza(banco, monitor, dpi=True)

    assert banco.registro.eventi == [("Elmetto assente", "AVVISO", "alert")]


def test_la_sorveglianza_dpi_e_attiva_solo_in_sosta():
    banco = _Banco(None, autonomy=False, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(banco.registro)

    _esegui_sorveglianza(banco, monitor, dpi=True)

    assert monitor.ricevuto["supervision_active"] is False


def test_senza_monitor_dpi_non_succede_niente():
    banco = _Banco(None, ultimo_comando={})

    _esegui_sorveglianza(banco, None, dpi=True)

    assert banco.registro.passi == []


def test_il_superamento_di_area_interdetta_arriva_all_orologio():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(
        banco.registro,
        [{"type": "restricted_area", "message": "Una persona in un'area vietata"}],
    )

    _esegui_sorveglianza(banco, monitor)

    assert banco.vision_loop.allarmi_inviati == [
        {
            "kind": "restricted_area",
            "message": "Una persona in un'area vietata",
            "level": "critical",
            "tipo": "AREA_VIETATA",
            "dettaglio": None,
        }
    ]


def test_i_dpi_mancanti_arrivano_all_orologio_come_avviso():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(
        banco.registro,
        [{
            "type": "dpi_missing",
            "message": "Manca l'elmetto",
            "missing": ("elmetto", "gilet"),
        }],
    )

    _esegui_sorveglianza(banco, monitor, dpi=True)

    inviato = banco.vision_loop.allarmi_inviati[0]
    assert inviato["kind"] == "dpi_missing"
    assert inviato["level"] == "warning"
    assert inviato["tipo"] == "DPI_MANCANTE"
    # L'orologio mostra l'elenco sull'LCD: iniziale maiuscola, piu' leggibile
    # di tutto maiuscolo mentre scorre.
    assert inviato["dettaglio"] == "Elmetto, Gilet"


def test_l_elenco_aggiornato_dei_dpi_arriva_all_orologio_senza_riga_nel_log():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(
        banco.registro,
        [{
            "type": "dpi_missing_update",
            "message": "Mancano ancora: gilet",
            "missing": ("gilet",),
        }],
    )

    _esegui_sorveglianza(banco, monitor, dpi=True)

    inviato = banco.vision_loop.allarmi_inviati[0]
    assert inviato["tipo"] == "DPI_MANCANTE"
    assert inviato["dettaglio"] == "Gilet"
    assert banco.registro.eventi == []


def test_la_caduta_non_arriva_all_orologio_ma_resta_critica():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(
        banco.registro, [{"type": "fall", "message": "Rilevata la caduta di una persona"}]
    )

    _esegui_sorveglianza(banco, monitor)

    inviato = banco.vision_loop.allarmi_inviati[0]
    assert inviato["level"] == "critical"
    # Scelta di progetto: chi e' caduto non guarda l'orologio, l'allarme serve
    # a chi sorveglia e resta nel Log degli alert.
    assert inviato["tipo"] is None


def test_l_area_di_nuovo_libera_spegne_l_allarme_senza_riga_nel_log():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})
    monitor = _Monitor(
        banco.registro,
        [{"type": "restricted_area_ok", "message": "Nessuna persona nell'area vietata"}],
    )

    _esegui_sorveglianza(banco, monitor)

    assert banco.vision_loop.allarmi_inviati[0]["tipo"] == "AREA_OK"
    assert banco.registro.eventi == []


def test_l_allarme_resta_nel_log_anche_se_il_canale_e_muto():
    banco = _Banco(None, ultimo_comando={"supervision_stop_active": True})

    def _canale_rotto(**_kwargs):
        raise RuntimeError("broker sparito")

    banco.vision_loop.publish_alarm = _canale_rotto
    monitor = _Monitor(
        banco.registro, [{"type": "fall", "message": "Rilevata la caduta di una persona"}]
    )

    _esegui_sorveglianza(banco, monitor)

    assert banco.registro.eventi == [
        ("Rilevata la caduta di una persona", "AVVISO", "alert")
    ]


def _run_all() -> int:
    tests = sorted(
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"[ OK ] {name}")

    print("-" * 60)
    print(f"Totale: {len(tests)}  |  passati: {passed}  |  falliti: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
