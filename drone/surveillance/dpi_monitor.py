from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional

from drone.surveillance.alarm_latch import AlarmLatch


_DPI_ITEMS: dict[str, tuple[str, str, str, str]] = {
    "helmet": (
        "elmetto",
        "Without_Helmet",
        "ALLERTA - elmetto mancante",
        "Manca l'elmetto di protezione",
    ),
    "goggles": (
        "occhiali",
        "Without_Goggles",
        "ALLERTA - occhiali mancanti",
        "Mancano gli occhiali protettivi",
    ),
    "vest": (
        "gilet",
        "Without_Vest",
        "ALLERTA - gilet mancante",
        "Manca il gilet ad alta visibilità",
    ),
    "shoes": (
        "scarpe",
        "Without_Safety_shoes",
        "ALLERTA - scarpe mancanti",
        "Mancano le scarpe antinfortunistiche",
    ),
}


def dpi_item_names(keys=None) -> tuple[str, ...]:
    if keys is None:
        keys = _DPI_ITEMS.keys()
    return tuple(
        _DPI_ITEMS[k][0] if k in _DPI_ITEMS else str(k) for k in keys
    )


def _count_label(detections_by_model: Iterable[dict], model_name: str, label: str) -> int:
    wanted = label.casefold()
    count = 0
    for entry in detections_by_model or []:
        if entry.get("name") != model_name:
            continue
        for det in entry.get("detections") or []:
            det_label = det.get("label")
            if det_label is None or str(det_label).casefold() != wanted:
                continue
            bbox = det.get("bbox")
            if bbox is None or len(bbox) != 4:
                continue
            count += 1
    return count


class DpiMonitor:
    def __init__(
        self,
        *,
        dpi_model_name: str = "personal_protection",
        required_items: Iterable[str] = (),
        alarm_after_sec: float = 0.0,
        clear_after_sec: float = 0.0,
        time_source: Optional[Callable[[], float]] = None,
    ):
        self.dpi_model_name = str(dpi_model_name)
        self.required_items = tuple(
            key for key in required_items if key in _DPI_ITEMS
        )
        self._latch = AlarmLatch(
            alarm_after_sec=alarm_after_sec,
            clear_after_sec=clear_after_sec,
            time_source=time_source,
        )
        self._now = time_source if time_source is not None else time.monotonic
        self._observing = False
        # Elenco dei DPI mancanti comunicato per ultimo all'orologio.
        self._missing_sent: tuple[str, ...] = ()

    def reset(self) -> None:
        self._latch.reset()
        self._observing = False
        self._missing_sent = ()

    def update(
        self,
        *,
        detections_by_model: Iterable[dict],
        supervision_active: bool,
    ) -> list[dict[str, Any]]:
        now = self._now()
        # Fuori dalla sosta il drone non osserva: gli allarmi restano come sono,
        # senza DPI_OK, perché non vedere più nessuno non vuol dire che i DPI
        # siano tornati. Li spegne una sosta successiva che non li vede più, o
        # la fine della missione.
        if not supervision_active:
            self._observing = False
            return []
        if not self._observing:
            self._latch.begin_observation(now)
            self._observing = True

        alarms: list[dict[str, Any]] = []
        era_attivo = any(self._latch.is_active(k) for k in self.required_items)
        for key in self.required_items:
            absent_label = _DPI_ITEMS[key][1]
            present = _count_label(detections_by_model, self.dpi_model_name, absent_label) > 0
            if self._latch.update(key, present, now):
                alarms.append(self._missing_alarm(key))
        # L'orologio mostra l'elenco completo di cio' che manca in quel momento,
        # non solo l'ultimo dispositivo: ogni allarme porta con se' la lista.
        mancanti = tuple(
            _DPI_ITEMS[k][0] for k in self.required_items if self._latch.is_active(k)
        )
        for alarm in alarms:
            alarm["missing"] = mancanti
        # L'orologio ha una sola causa DPI: DPI_OK parte solo quando TUTTI i DPI
        # sono rientrati, altrimenti spegnerebbe l'allarme con un gilet ancora mancante.
        if era_attivo and not any(self._latch.is_active(k) for k in self.required_items):
            alarms.append({
                "type": "dpi_ok",
                "title": "DPI di nuovo presenti",
                "message": "Tutti i DPI sono di nuovo presenti",
            })
        # Un DPI rientrato mentre altri mancano ancora non genera allarmi. Senza
        # questo aggiornamento l'orologio continuerebbe a elencarlo fra i mancanti.
        elif not alarms and mancanti and mancanti != self._missing_sent:
            alarms.append({
                "type": "dpi_missing_update",
                "title": "Elenco dei DPI mancanti aggiornato",
                "message": f"Mancano ancora: {', '.join(mancanti)}",
                "missing": mancanti,
            })
        self._missing_sent = mancanti
        return alarms

    def _missing_alarm(self, key: str) -> dict[str, Any]:
        _nome, _absent_label, title, message = _DPI_ITEMS[key]
        return {
            "type": "dpi_missing",
            "item": key,
            "title": title,
            "message": message,
        }
