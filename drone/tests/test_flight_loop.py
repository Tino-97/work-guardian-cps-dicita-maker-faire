from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from drone.flight import flight_loop


class _Registro:
    def __init__(self):
        self.passi: list[str] = []
        self.eventi: list[tuple[str, str]] = []
        self.guardie: list[dict] = []
        self.detection: list[bool] = []
        self.cambio_scenario = False


class _Controller:
    def __init__(self, registro, *, flying=True, rompe=False):
        self._registro = registro
        self._rompe = rompe
        self.is_flying = flying

    def land(self):
        self._registro.passi.append("land")
        if self._rompe:
            raise RuntimeError("land")
        self.is_flying = False


class _PilotCommands:
    def __init__(self, registro, **over):
        self._registro = registro
        self._giri = over.get("giri", 1)
        self._autonomy = over.get("autonomy", False)
        self._detection = over.get("detection", False)
        self._autonomia_off_al_giro = over.get("autonomia_off_al_giro")
        self._esplode = over.get("esplode")
        self.giro = 0
        self.comandi_manuali = 0

    def step(self):
        self.giro += 1
        self._registro.passi.append(f"step{self.giro}")
        if self._esplode is not None:
            raise self._esplode
        if self.giro == self._autonomia_off_al_giro:
            self._autonomy = False
        return self.giro <= self._giri

    def is_autonomy_enabled(self):
        return self._autonomy

    def is_scenario_change_requested(self):
        if not self._registro.cambio_scenario:
            return False
        self._registro.cambio_scenario = False
        return True

    def is_detection_enabled(self):
        return self._detection

    def disable_autonomy(self):
        self._autonomy = False
        self._registro.passi.append("disable_autonomy")

    def send_manual_command(self):
        self.comandi_manuali += 1
        self._registro.passi.append("manuale")
        return {"lr": 7}


class _VisionLoop:
    def __init__(self, registro, *, richiede_detection=False, esiti=()):
        self._registro = registro
        self._richiede_detection = richiede_detection
        self._esiti = list(esiti)
        self.cached_status = {"battery": 100}
        self.last_autopilot_command = None

    def step(self, controller, *, run_detection, autonomy_enabled, dashboard):
        self._registro.passi.append("vision")
        self._registro.detection.append(run_detection)
        return self._esiti.pop(0) if self._esiti else True

    def autopilot_requests_detection(self):
        return self._richiede_detection

    def clear_autopilot_overlay(self):
        self._registro.passi.append("clear_overlay")


class _Dashboard:
    def __init__(self, registro):
        self._registro = registro

    def clear_autopilot(self):
        self._registro.passi.append("dashboard_clear")


class _SafetyNetMonitor:
    def __init__(self, registro):
        self._registro = registro

    def reset(self):
        self._registro.passi.append("safety_net_reset")


class _Clock:
    def __init__(self, registro):
        self._registro = registro

    def tick(self, fps):
        self._registro.passi.append(f"tick{fps}")


class _Screen:
    def __init__(self, registro):
        self._registro = registro

    def fill(self, colore):
        self._registro.passi.append("fill")


_PATCH = (
    "enable_high_dpi_awareness",
    "ensure_utf8_console",
    "configure_logging",
    "run_startup",
    "start_mqtt_client",
    "update_watch_mission",
    "arm_mission",
    "release_mission",
    "setup_video_window",
    "mark_runtime_started",
    "discard_button_presses",
    "reset_mission_state",
    "run_postflight",
    "apply_battery_guard",
    "apply_desync_guard",
    "handle_autonomy_step",
    "handle_person_step",
    "handle_dpi_step",
    "print_event",
    "pygame",
)


class _Banco:
    def __init__(self, **over):
        self.registro = _Registro()
        self.over = over
        self.controller = _Controller(
            self.registro,
            flying=over.get("flying", True),
            rompe=over.get("land_rompe", False),
        )
        self.pilot_commands = _PilotCommands(self.registro, **over)
        self.vision_loop = _VisionLoop(
            self.registro,
            richiede_detection=over.get("richiede_detection", False),
            esiti=over.get("vision_esiti", ()),
        )
        self.dashboard = _Dashboard(self.registro)
        self.safety_net_monitor = _SafetyNetMonitor(self.registro) if over.get("safety_net", True) else None
        self.batteria = list(over.get("batteria", ()))
        self.autonomia_esiti = list(over.get("autonomia_esiti", ()))
        self.autonomia_disinnesca = over.get("autonomia_disinnesca", False)
        self.missione_completata = over.get("missione_completata", False)

    def _run_startup(self, subsystems, original_stdout):
        self.registro.passi.append("startup")
        if not self.over.get("startup_ok", True):
            return False
        subsystems.controller = self.controller
        subsystems.dashboard = self.dashboard
        subsystems.screen = _Screen(self.registro) if self.over.get("screen", False) else None
        subsystems.clock = _Clock(self.registro) if self.over.get("screen", False) else None
        return True

    def _arm_mission(self, subsystems):
        self.registro.passi.append("armamento")
        self.armamenti = getattr(self, "armamenti", 0) + 1
        if self.armamenti > self.over.get("scenari", 1):
            return False
        subsystems.pilot_commands = self.pilot_commands
        subsystems.vision_loop = self.vision_loop
        subsystems.safety_net_monitor = self.safety_net_monitor
        return True

    def _release_mission(self, subsystems):
        self.registro.passi.append("rilascio")

    def _apply_battery_guard(self, subsystems, *, rth_active, rth_operator_override, last_low_battery_warn_at):
        self.registro.passi.append("guardia_batteria")
        self.registro.guardie.append(
            {"rth_active": rth_active, "rth_operator_override": rth_operator_override}
        )
        if self.batteria:
            return self.batteria.pop(0)
        return True, rth_active, last_low_battery_warn_at

    def _apply_desync_guard(self, subsystems, ground_since):
        self.registro.passi.append("guardia_desync")
        return ground_since

    def _handle_autonomy_step(self, **kwargs):
        self.registro.passi.append("autonomia")
        if self.missione_completata:
            kwargs["vision_loop"].last_autopilot_command = {"finished": True}
        if self.autonomia_disinnesca:
            kwargs["pilot_commands"].disable_autonomy()
        return self.autonomia_esiti.pop(0) if self.autonomia_esiti else True

    def esegui(self):
        originali = {nome: getattr(flight_loop, nome) for nome in _PATCH}
        stdout_originale = sys.stdout
        flight_loop.enable_high_dpi_awareness = lambda: None
        flight_loop.ensure_utf8_console = lambda: None
        flight_loop.configure_logging = lambda: None
        flight_loop.run_startup = self._run_startup
        flight_loop.start_mqtt_client = lambda: self.registro.passi.append("mqtt_avvio")
        flight_loop.update_watch_mission = lambda flying: self.registro.passi.append(
            "missione_in_volo" if flying else "missione_a_terra"
        )
        flight_loop.arm_mission = self._arm_mission
        flight_loop.release_mission = self._release_mission
        flight_loop.setup_video_window = lambda dashboard: self.registro.passi.append("finestra")
        flight_loop.mark_runtime_started = lambda: self.registro.passi.append("avvio")
        flight_loop.discard_button_presses = lambda: self.registro.passi.append("coda_pulsanti_svuotata")
        flight_loop.reset_mission_state = lambda: self.registro.passi.append("reset_missione")
        flight_loop.run_postflight = lambda s, out: self.registro.passi.append("postflight")
        flight_loop.apply_battery_guard = self._apply_battery_guard
        flight_loop.apply_desync_guard = self._apply_desync_guard
        flight_loop.handle_autonomy_step = self._handle_autonomy_step
        flight_loop.handle_person_step = lambda **kw: self.registro.passi.append("persone")
        flight_loop.handle_dpi_step = lambda **kw: self.registro.passi.append("dpi")
        flight_loop.print_event = lambda msg, *, prefix="EVENTO": self.registro.eventi.append(
            (str(msg), prefix)
        )
        flight_loop.pygame = type(
            "_Pygame",
            (),
            {
                "display": type(
                    "_Display",
                    (),
                    {
                        "set_caption": staticmethod(lambda titolo: None),
                        "iconify": staticmethod(lambda: None),
                        "flip": staticmethod(lambda: self.registro.passi.append("flip")),
                    },
                )
            },
        )
        try:
            flight_loop.main()
        finally:
            for nome, valore in originali.items():
                setattr(flight_loop, nome, valore)
            sys.stdout = stdout_originale
        return self.registro


def _messaggi(registro) -> list[str]:
    return [msg for msg, _ in registro.eventi]


def test_l_avvio_annullato_non_arma_nessuna_missione():
    registro = _Banco(startup_ok=False).esegui()

    assert "armamento" not in registro.passi
    assert "step1" not in registro.passi
    assert "finestra" not in registro.passi


def test_la_chiusura_avviene_anche_se_l_avvio_e_annullato():
    registro = _Banco(startup_ok=False).esegui()

    assert registro.passi[-1] == "postflight"


def test_lo_scenario_annullato_chiude_senza_volare():
    registro = _Banco(scenari=0).esegui()

    assert registro.passi.count("armamento") == 1
    assert "step1" not in registro.passi
    assert registro.passi[-1] == "postflight"


def test_senza_richiesta_di_scenario_il_volo_finisce_la_sessione():
    registro = _Banco().esegui()

    assert "rilascio" not in registro.passi
    assert registro.passi.count("armamento") == 1
    assert registro.passi[-1] == "postflight"


def test_la_finestra_si_apre_prima_del_ciclo():
    registro = _Banco().esegui()

    assert registro.passi.index("finestra") < registro.passi.index("step1")
    assert registro.passi.index("avvio") < registro.passi.index("step1")


def test_le_pressioni_arrivate_prima_del_ciclo_non_vengono_eseguite():
    registro = _Banco().esegui()

    assert registro.passi.index("finestra") < registro.passi.index("coda_pulsanti_svuotata")
    assert registro.passi.index("coda_pulsanti_svuotata") < registro.passi.index("step1")


def test_un_ctrl_c_chiude_senza_propagare():
    registro = _Banco(esplode=KeyboardInterrupt()).esegui()

    assert registro.passi[-1] == "postflight"
    assert "tastiera" in _messaggi(registro)[0]


def test_un_errore_imprevisto_viene_rilanciato_dopo_la_chiusura():
    banco = _Banco(esplode=RuntimeError("guasto"))

    try:
        banco.esegui()
    except RuntimeError as exc:
        assert "guasto" in str(exc)
    else:
        raise AssertionError("l'errore doveva essere rilanciato")

    assert banco.registro.passi[-1] == "postflight"
    assert banco.registro.eventi[0][1] == "ERRORE"


def test_in_autonomia_non_si_inviano_comandi_manuali():
    registro = _Banco(autonomy=True).esegui()

    assert "autonomia" in registro.passi
    assert "manuale" not in registro.passi


def test_in_manuale_non_gira_l_autopilota():
    banco = _Banco(autonomy=False)
    registro = banco.esegui()

    assert "autonomia" not in registro.passi
    assert banco.pilot_commands.comandi_manuali == 1


def test_in_manuale_la_mappa_e_il_monitor_reti_si_azzerano():
    registro = _Banco(autonomy=False).esegui()

    assert "clear_overlay" in registro.passi
    assert "dashboard_clear" in registro.passi
    assert "reset_missione" in registro.passi
    assert "safety_net_reset" in registro.passi


def test_in_manuale_senza_monitor_reti_non_si_azzera_niente():
    registro = _Banco(autonomy=False, safety_net=False).esegui()

    assert "safety_net_reset" not in registro.passi
    assert "manuale" in registro.passi


def test_la_presa_di_controllo_annulla_il_rientro():
    registro = _Banco(
        autonomy=True,
        giri=2,
        autonomia_off_al_giro=2,
        batteria=[(True, True, 0.0)],
    ).esegui()

    assert "Rientro alla home annullato" in _messaggi(registro)
    assert registro.eventi[0][1] == "AVVISO"


def test_dopo_la_presa_di_controllo_il_rientro_non_si_reingaggia():
    registro = _Banco(
        autonomy=True,
        giri=2,
        autonomia_off_al_giro=2,
        batteria=[(True, True, 0.0)],
    ).esegui()

    assert registro.guardie[0]["rth_operator_override"] is False
    assert registro.guardie[1]["rth_operator_override"] is True
    assert registro.guardie[1]["rth_active"] is False


def test_la_presa_di_controllo_lascia_il_drone_al_pilota():
    banco = _Banco(
        autonomy=True,
        giri=2,
        autonomia_off_al_giro=2,
        batteria=[(True, True, 0.0)],
    )
    registro = banco.esegui()

    assert "land" not in registro.passi
    assert banco.controller.is_flying is True
    assert banco.pilot_commands.comandi_manuali == 1


def test_senza_presa_di_controllo_il_rientro_resta_attivo():
    registro = _Banco(autonomy=True, giri=2, batteria=[(True, True, 0.0)]).esegui()

    assert "Rientro alla home annullato" not in _messaggi(registro)
    assert registro.guardie[1]["rth_active"] is True
    assert registro.guardie[1]["rth_operator_override"] is False


def test_il_rientro_interrotto_da_un_fault_atterra_sul_posto():
    banco = _Banco(
        autonomy=True,
        batteria=[(True, True, 0.0)],
        autonomia_disinnesca=True,
    )
    registro = banco.esegui()

    assert "land" in registro.passi
    assert banco.controller.is_flying is False
    assert ("Rientro interrotto: atterraggio", "ERRORE") in registro.eventi
    assert "clear_overlay" in registro.passi


def test_il_rientro_completato_atterra_alla_home_e_lo_dice():
    banco = _Banco(
        autonomy=True,
        batteria=[(True, True, 0.0)],
        autonomia_disinnesca=True,
        missione_completata=True,
    )
    registro = banco.esegui()

    assert "land" in registro.passi
    assert banco.controller.is_flying is False
    assert ("Home raggiunta: atterraggio", "EVENTO") in registro.eventi
    assert "Rientro interrotto: atterraggio" not in _messaggi(registro)


def test_un_atterraggio_fallito_dopo_il_rientro_chiude_comunque():
    registro = _Banco(
        autonomy=True,
        batteria=[(True, True, 0.0)],
        autonomia_disinnesca=True,
        land_rompe=True,
    ).esegui()

    assert registro.passi[-1] == "postflight"


def test_a_terra_il_rientro_interrotto_non_atterra():
    registro = _Banco(
        autonomy=True,
        flying=False,
        batteria=[(True, True, 0.0)],
        autonomia_disinnesca=True,
    ).esegui()

    assert "land" not in registro.passi


def test_la_detection_e_forzata_dall_autopilota_in_sosta():
    registro = _Banco(autonomy=True, detection=False, richiede_detection=True).esegui()

    assert registro.detection == [True]


def test_in_manuale_l_autopilota_non_puo_forzare_la_detection():
    registro = _Banco(autonomy=False, detection=False, richiede_detection=True).esegui()

    assert registro.detection == [False]


def test_la_detection_del_pilota_basta_da_sola():
    registro = _Banco(autonomy=False, detection=True, richiede_detection=False).esegui()

    assert registro.detection == [True]


def test_senza_richieste_la_detection_resta_spenta():
    registro = _Banco(autonomy=True, detection=False, richiede_detection=False).esegui()

    assert registro.detection == [False]


def test_la_guardia_batteria_precede_quella_di_desincronizzazione():
    registro = _Banco().esegui()

    assert registro.passi.index("guardia_batteria") < registro.passi.index("guardia_desync")


def test_le_guardie_girano_dopo_il_video():
    registro = _Banco().esegui()

    assert registro.passi.index("vision") < registro.passi.index("guardia_batteria")


def test_la_batteria_critica_chiude_il_volo():
    registro = _Banco(autonomy=True, giri=5, batteria=[(False, False, 0.0)]).esegui()

    assert "guardia_desync" not in registro.passi
    assert "autonomia" not in registro.passi
    assert "persone" not in registro.passi
    assert registro.passi[-1] == "postflight"


def test_un_video_che_si_ferma_chiude_il_volo():
    registro = _Banco(giri=5, vision_esiti=[False]).esegui()

    assert "guardia_batteria" not in registro.passi
    assert registro.passi[-1] == "postflight"


def test_la_fine_della_missione_chiude_il_volo():
    registro = _Banco(autonomy=True, giri=5, autonomia_esiti=[False]).esegui()

    assert "persone" not in registro.passi
    assert registro.passi[-1] == "postflight"


def test_la_sorveglianza_gira_anche_in_manuale():
    registro = _Banco(autonomy=False).esegui()

    assert "persone" in registro.passi
    assert "dpi" in registro.passi


def test_la_sorveglianza_gira_anche_in_autonomia():
    registro = _Banco(autonomy=True).esegui()

    assert "persone" in registro.passi
    assert "dpi" in registro.passi


def test_il_collegamento_mqtt_si_apre_una_volta_prima_delle_missioni():
    registro = _Banco(scenari=2).esegui()

    assert registro.passi.count("mqtt_avvio") == 1
    assert registro.passi.index("startup") < registro.passi.index("mqtt_avvio")
    assert registro.passi.index("mqtt_avvio") < registro.passi.index("armamento")


def test_l_avvio_annullato_non_apre_il_collegamento_mqtt():
    registro = _Banco(startup_ok=False).esegui()

    assert "mqtt_avvio" not in registro.passi


def test_a_ogni_giro_l_orologio_riceve_lo_stato_del_volo():
    registro = _Banco(giri=2).esegui()

    assert registro.passi.index("missione_in_volo") < registro.passi.index("step1")
    assert registro.passi.count("missione_in_volo") == 4


def test_a_terra_la_missione_dell_orologio_non_parte():
    registro = _Banco(flying=False).esegui()

    assert "missione_in_volo" not in registro.passi
    assert "missione_a_terra" in registro.passi


def test_l_atterraggio_che_chiude_il_volo_chiude_la_missione_dell_orologio():
    registro = _Banco(
        autonomy=True,
        batteria=[(True, True, 0.0)],
        autonomia_disinnesca=True,
    ).esegui()

    assert registro.passi.index("land") < registro.passi.index("missione_a_terra")
    assert registro.passi.index("missione_a_terra") < registro.passi.index("postflight")


def test_la_finestra_pygame_si_aggiorna_a_ogni_giro():
    registro = _Banco(screen=True).esegui()

    assert "fill" in registro.passi
    assert "flip" in registro.passi
    assert f"tick{flight_loop.APP_CONFIG.loop_hz}" in registro.passi


def test_senza_finestra_pygame_il_ciclo_gira_lo_stesso():
    registro = _Banco(screen=False).esegui()

    assert "flip" not in registro.passi
    assert registro.passi[-1] == "postflight"


def test_il_ritorno_allo_scenario_rilascia_e_riarma():
    banco = _Banco(scenari=2)
    banco.registro.cambio_scenario = True
    registro = banco.esegui()

    passi = [p for p in registro.passi if p in ("armamento", "rilascio", "postflight")]
    assert passi == ["armamento", "rilascio", "armamento", "postflight"]


def test_il_ritorno_allo_scenario_riapre_la_finestra_del_volo():
    banco = _Banco(scenari=2)
    banco.registro.cambio_scenario = True
    registro = banco.esegui()

    assert registro.passi.count("finestra") == 2


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
