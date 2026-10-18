from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Mapping, Optional

_REASON_SUPERVISION_COMPLETED = "supervision_stop_completed"


class SafetyNetMonitor:
    def __init__(
        self,
        waypoint_tag_map: Mapping[int, Iterable[int]],
        safety_net_model_name: str = "collective_protection",
        *,
        safety_net_confirm_sec: float = 0.0,
        time_source: Optional[Callable[[], float]] = None,
    ):
        self.safety_net_model_name = str(safety_net_model_name)
        self._safety_net_confirm_sec = max(0.0, float(safety_net_confirm_sec))
        self._now = time_source if time_source is not None else time.monotonic
        self._tag_map: dict[int, frozenset[int]] = {}
        for waypoint_number, tag_ids in dict(waypoint_tag_map or {}).items():
            tags = frozenset(int(t) for t in tag_ids)
            if tags:
                self._tag_map[int(waypoint_number)] = tags

        self._episode_waypoint: Optional[int] = None
        self._saw_reference_tag = False
        self._saw_safety_net_any = False
        self._safety_net_streak_start: Optional[float] = None
        self._safety_net_streak_max = 0.0
        self._seen_tags: set[int] = set()

    def _clear_episode_state(self) -> None:
        self._saw_reference_tag = False
        self._saw_safety_net_any = False
        self._safety_net_streak_start = None
        self._safety_net_streak_max = 0.0
        self._seen_tags = set()

    def reset(self) -> None:
        self._episode_waypoint = None
        self._clear_episode_state()

    def update(
        self,
        *,
        target_index: Optional[int],
        supervision_stop_active: bool,
        reason: str,
        fault: bool,
        visible_tag_ids: Iterable[int],
        safety_net_detected: bool,
    ) -> Optional[dict[str, Any]]:
        waypoint_number = None if target_index is None else int(target_index) + 1
        mapped_tags = (
            self._tag_map.get(waypoint_number) if waypoint_number is not None else None
        )

        if (
            reason == _REASON_SUPERVISION_COMPLETED
            and self._episode_waypoint is not None
            and waypoint_number == self._episode_waypoint
        ):
            if mapped_tags is not None:
                self._accumulate(mapped_tags, visible_tag_ids, safety_net_detected)
            verdict = self._finalize(self._episode_waypoint)
            self.reset()
            return verdict

        if fault and self._episode_waypoint is not None:
            self.reset()
            return None

        if supervision_stop_active and mapped_tags is not None:
            if self._episode_waypoint != waypoint_number:
                self._begin_episode(waypoint_number)
            self._accumulate(mapped_tags, visible_tag_ids, safety_net_detected)
            return None

        if self._episode_waypoint is not None:
            self._safety_net_streak_start = None
        return None

    def _begin_episode(self, waypoint_number: int) -> None:
        self._episode_waypoint = waypoint_number
        self._clear_episode_state()

    def _accumulate(
        self,
        mapped_tags: frozenset[int],
        visible_tag_ids: Iterable[int],
        safety_net_detected: bool,
    ) -> None:
        seen = mapped_tags & {int(t) for t in visible_tag_ids}
        if seen:
            self._saw_reference_tag = True
            self._seen_tags |= seen
        now = self._now()
        if safety_net_detected:
            self._saw_safety_net_any = True
            if self._safety_net_streak_start is None:
                self._safety_net_streak_start = now
            streak = now - self._safety_net_streak_start
            if streak > self._safety_net_streak_max:
                self._safety_net_streak_max = streak
        else:
            self._safety_net_streak_start = None

    def _finalize(self, waypoint_number: int) -> dict[str, Any]:
        reference_tags = self._tag_map.get(waypoint_number, frozenset())
        saw_safety_net = self._saw_safety_net_any and self._safety_net_streak_max >= self._safety_net_confirm_sec
        if not self._saw_reference_tag:
            outcome = "no_tags"
        elif saw_safety_net:
            outcome = "present"
        else:
            outcome = "missing"
        return {
            "waypoint": waypoint_number,
            "outcome": outcome,
            "tags": sorted(reference_tags),
            "seen_tags": sorted(self._seen_tags),
        }
