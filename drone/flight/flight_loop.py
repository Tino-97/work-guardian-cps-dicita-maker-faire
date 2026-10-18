from __future__ import annotations

import logging
import sys
from typing import Optional

import pygame

from drone.config import APP_CONFIG
from drone.flight.guards import apply_battery_guard, apply_desync_guard
from drone.flight.steps import handle_autonomy_step, handle_dpi_step, handle_person_step
from drone.config.logging_setup import ensure_utf8_console, configure_logging
from drone.flight.postflight import release_mission, run_postflight
from drone.flight.preflight import Subsystems, arm_mission, run_startup
from drone.hardware.joystick import discard_button_presses
from drone.perception.vision_loop import start_mqtt_client, update_watch_mission
from drone.ui.console import (
    print_event,
    mark_runtime_started,
    reset_mission_state,
)
from drone.ui.video.window import enable_high_dpi_awareness, setup_video_window

LOGGER = logging.getLogger(__name__)


def main():
    enable_high_dpi_awareness()

    ensure_utf8_console()

    configure_logging()

    subsystems = Subsystems()
    original_stdout = sys.stdout

    try:
        if not run_startup(subsystems, original_stdout):
            return

        start_mqtt_client()

        while True:
            if not arm_mission(subsystems):
                break

            setup_video_window(subsystems.dashboard)

            pygame.display.set_caption(APP_CONFIG.controller_window_title)
            pygame.display.iconify()

            mark_runtime_started()

            discard_button_presses()

            if not run_flight(subsystems):
                break

            release_mission(subsystems)

    except KeyboardInterrupt:
        print_event("Interrotto da tastiera (Ctrl+C)")
    except Exception:
        print_event("Errore imprevisto del programma", prefix="ERRORE")
        raise
    finally:
        run_postflight(subsystems, original_stdout)


def run_flight(subsystems: Subsystems) -> bool:
    pilot_commands = subsystems.pilot_commands
    vision_loop = subsystems.vision_loop
    controller = subsystems.controller
    dashboard = subsystems.dashboard
    apriltag_autopilot = subsystems.apriltag_autopilot
    safety_net_monitor = subsystems.safety_net_monitor
    person_monitor = subsystems.person_monitor
    dpi_monitor = subsystems.dpi_monitor
    flight_data_logger = subsystems.flight_data_logger
    screen = subsystems.screen
    clock = subsystems.clock

    ground_since: Optional[float] = None
    last_low_battery_warn_at = 0.0
    rth_active = False
    rth_operator_override = False

    running = True
    while running:
        # La missione dell'orologio segue il volo: il decollo la avvia e
        # qualunque atterraggio, del pilota, automatico o per batteria, la chiude.
        update_watch_mission(controller.is_flying)
        if not controller.is_flying:
            # A terra la missione dell'orologio è chiusa e FINE_MISSIONE ne
            # azzera gli allarmi. Gli allarmi DPI e area restano accesi fra una
            # sosta e l'altra: senza azzerarli anche qui, al decollo successivo
            # uno rimasto acceso impedirebbe di segnalare quello nuovo.
            if person_monitor is not None:
                person_monitor.reset()
            if dpi_monitor is not None:
                dpi_monitor.reset()

        autonomy_before_step = pilot_commands.is_autonomy_enabled()
        running = pilot_commands.step()
        if not running:
            continue

        if rth_active and autonomy_before_step and not pilot_commands.is_autonomy_enabled():
            rth_active = False
            rth_operator_override = True
            print_event("Rientro alla home annullato", prefix="AVVISO")

        autopilot_forced_detection = (
            pilot_commands.is_autonomy_enabled()
            and vision_loop.autopilot_requests_detection()
        )

        running = vision_loop.step(
            controller,
            run_detection=(
                pilot_commands.is_detection_enabled()
                or autopilot_forced_detection
            ),
            autonomy_enabled=pilot_commands.is_autonomy_enabled(),
            dashboard=dashboard,
        )
        if not running:
            continue

        running, rth_active, last_low_battery_warn_at = apply_battery_guard(
            subsystems,
            rth_active=rth_active,
            rth_operator_override=rth_operator_override,
            last_low_battery_warn_at=last_low_battery_warn_at,
        )
        if not running:
            continue

        ground_since = apply_desync_guard(subsystems, ground_since)

        if pilot_commands.is_autonomy_enabled():
            running = handle_autonomy_step(
                pilot_commands=pilot_commands,
                apriltag_autopilot=apriltag_autopilot,
                vision_loop=vision_loop,
                dashboard=dashboard,
                flight_data_logger=flight_data_logger,
                controller=controller,
                safety_net_monitor=safety_net_monitor,
            )
            if not running:
                continue
        else:
            vision_loop.clear_autopilot_overlay()
            dashboard.clear_autopilot()
            reset_mission_state()
            pilot_commands.send_manual_command()
            if safety_net_monitor is not None:
                safety_net_monitor.reset()

        handle_person_step(
            person_monitor=person_monitor,
            vision_loop=vision_loop,
            pilot_commands=pilot_commands,
        )

        handle_dpi_step(
            dpi_monitor=dpi_monitor,
            vision_loop=vision_loop,
            pilot_commands=pilot_commands,
        )

        if rth_active and controller.is_flying and not pilot_commands.is_autonomy_enabled():
            rth_completed = bool(
                (vision_loop.last_autopilot_command or {}).get("finished", False)
            )
            print_event(
                "Home raggiunta: atterraggio"
                if rth_completed
                else "Rientro interrotto: atterraggio",
                prefix="EVENTO" if rth_completed else "ERRORE",
            )
            vision_loop.clear_autopilot_overlay()
            try:
                controller.land()
            except Exception:
                LOGGER.warning("L'atterraggio dopo il rientro alla home non è riuscito", exc_info=True)
            running = False
            continue

        if screen is not None:
            screen.fill((30, 30, 30))
            pygame.display.flip()

        if clock is not None:
            clock.tick(APP_CONFIG.loop_hz)

    # Gli atterraggi che chiudono il volo escono dal ciclo prima del giro successivo.
    update_watch_mission(controller.is_flying)
    return pilot_commands.is_scenario_change_requested()
