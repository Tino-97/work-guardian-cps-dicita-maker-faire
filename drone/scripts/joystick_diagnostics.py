import logging

import os

import sys

import time

import pygame

LOGGER = logging.getLogger(__name__)


DEADZONE = 0.15


def axis_value(v, deadzone=DEADZONE):
    return 0.0 if abs(v) < deadzone else v


def _event_ids(event):
    ids = []

    for attr_name in ("instance_id", "joy", "which"):
        attr_value = getattr(event, attr_name, None)

        if attr_value is not None and attr_value not in ids:
            ids.append(f"{attr_name}={attr_value}")

    return ", ".join(ids) if ids else "nessun-id"


def _connect_joystick(device_index=0):
    if pygame.joystick.get_count() == 0:
        return None, None, None

    if not 0 <= device_index < pygame.joystick.get_count():
        device_index = 0

    joystick = pygame.joystick.Joystick(device_index)
    joystick.init()

    try:
        instance_id = joystick.get_instance_id()
    except Exception:
        instance_id = None

    try:
        legacy_id = joystick.get_id()
    except Exception:
        legacy_id = None

    return joystick, instance_id, legacy_id


def _disconnect_joystick(joystick):
    if joystick is None:
        return

    try:
        joystick.quit()
    except Exception:
        pass


def _print_controller_info(joystick, instance_id, legacy_id):
    if joystick is None:
        print("Nessun controller disponibile.")
        return

    print("=" * 60)
    print("CONTROLLER RILEVATO")
    print(f"Nome       : {joystick.get_name()}")
    print(f"Assi       : {joystick.get_numaxes()}")
    print(f"Pulsanti   : {joystick.get_numbuttons()}")
    print(f"Hat/D-pad  : {joystick.get_numhats()}")
    print(f"instance_id: {instance_id}")
    print(f"legacy_id  : {legacy_id}")
    print("=" * 60)
    print("Premi i pulsanti del controller per vederne l'indice.")
    print("Muovi gli stick per vedere i valori degli assi.")
    print("Chiudi la finestra oppure premi ESC per uscire.")
    print("Di ogni evento sono stampati anche gli identificativi del controller.")
    print("=" * 60)


def _get_event_controller_ids(event):
    ids = []
    for attr_name in ("instance_id", "joy", "which"):
        attr_value = getattr(event, attr_name, None)
        if attr_value is None:
            continue
        if attr_value not in ids:
            ids.append(attr_value)
    return tuple(ids)


def _event_matches_active_joystick(event, active_instance_id, active_legacy_id):
    event_ids = _get_event_controller_ids(event)

    if active_instance_id is not None and active_instance_id in event_ids:
        return True

    if active_legacy_id is not None and active_legacy_id in event_ids:
        return True

    try:
        if pygame.joystick.get_count() <= 1:
            LOGGER.debug(
                "Evento joystick accettato dal criterio di compatibilità | event_ids=%s | instance_id=%s | legacy_id=%s",
                event_ids,
                active_instance_id,
                active_legacy_id,
            )
            return True
    except pygame.error:
        pass

    return False


def ensure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main():
    ensure_utf8_console()
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

    pygame.init()
    pygame.joystick.init()

    screen = pygame.display.set_mode((760, 220))
    pygame.display.set_caption("WORK-GUARDIAN - Diagnostica del controller")

    if pygame.joystick.get_count() == 0:
        print("Nessun controller rilevato.")
        print("Collega il controller e rilancia lo script.")
        pygame.quit()
        return

    joystick, instance_id, legacy_id = _connect_joystick(0)
    _print_controller_info(joystick, instance_id, legacy_id)

    clock = pygame.time.Clock()

    last_axis_print = 0.0

    running = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

            elif event.type == pygame.JOYBUTTONDOWN:
                if _event_matches_active_joystick(event, instance_id, legacy_id):
                    print(f"[PULSANTE PREMUTO]    indice={event.button} | {_event_ids(event)}")

            elif event.type == pygame.JOYBUTTONUP:
                if _event_matches_active_joystick(event, instance_id, legacy_id):
                    print(f"[PULSANTE RILASCIATO] indice={event.button} | {_event_ids(event)}")

            elif event.type == pygame.JOYHATMOTION:
                if _event_matches_active_joystick(event, instance_id, legacy_id):
                    print(f"[D-PAD]               indice={event.hat} valore={event.value} | {_event_ids(event)}")

            elif event.type == pygame.JOYDEVICEADDED:
                print(
                    f"[INFO] Controller collegato: device_index={getattr(event, 'device_index', None)} | {_event_ids(event)}"
                )

                if joystick is None:
                    device_index = getattr(event, "device_index", getattr(event, "which", 0))
                    joystick, instance_id, legacy_id = _connect_joystick(device_index)
                    _print_controller_info(joystick, instance_id, legacy_id)

            elif event.type == pygame.JOYDEVICEREMOVED:
                print(f"[INFO] Controller scollegato: {_event_ids(event)}")

                if joystick is not None and _event_matches_active_joystick(event, instance_id, legacy_id):
                    _disconnect_joystick(joystick)
                    joystick = None
                    instance_id = None
                    legacy_id = None

                    if pygame.joystick.get_count() > 0:
                        joystick, instance_id, legacy_id = _connect_joystick(0)
                        _print_controller_info(joystick, instance_id, legacy_id)

        now = time.time()
        if now - last_axis_print > 0.2 and joystick is not None:
            try:
                axis_values = []

                for i in range(joystick.get_numaxes()):
                    v = axis_value(joystick.get_axis(i))
                    axis_values.append(f"asse {i}: {v:+.3f}")

                print("[ASSI] " + " | ".join(axis_values))
                last_axis_print = now

            except Exception:
                print("[AVVISO] Lettura degli assi non riuscita: il controller non è più disponibile.")
                _disconnect_joystick(joystick)
                joystick = None
                instance_id = None
                legacy_id = None

        screen.fill((30, 30, 30))
        pygame.display.flip()

        clock.tick(60)

    _disconnect_joystick(joystick)
    pygame.joystick.quit()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
