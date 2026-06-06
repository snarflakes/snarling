"""
trigger_scheduler.py — Decide when to wake the agent.

Only two triggers wake the agent:

1. **presence_settled** — someone arrived and has been stable for a while.
   The most valuable moment to interpret the environment.

2. **observation_report** — periodic scheduled check.
   Active (present): every 30 min.  Inactive (absent): every 2–4 h.

All other thermal events (source appeared, disappeared, measurement changed)
accumulate in the world state without waking the agent.

Thread-safe: all mutations go through a :class:`threading.Lock`.

5-second dedup: if a trigger was emitted within the last 5 seconds, the
next one is suppressed. This prevents double-wakeups from simultaneous
presence transitions.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TriggerEvent:
    """An event that wakes the agent."""

    trigger_reason: str               # "presence_settled" or "observation_report"
    present: bool                     # whether someone is currently present
    absent_duration: str               # human-readable, e.g. "5m 30s" or "unknown"
    absent_duration_sec: Optional[float]  # seconds, or None if never absent
    world_state: dict                 # current world state snapshot
    changes_since_last: dict          # diff since last agent wake
    timestamp: str                    # ISO 8601


def _format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


class TriggerScheduler:
    """Two triggers wake the agent. Nothing else does.

    Trigger A: ``presence_settled`` — the user is now here and stable.
    Trigger B: ``observation_report`` — scheduled check (adaptive interval).

    Scheduled triggers use **wall-clock time**, not frame counting.
    Call :meth:`on_scheduled` whenever you want to check — it will only
    fire when enough wall-clock time has elapsed since the last trigger.

    Typical day: 2–4 ``presence_settled`` + ~16–48 ``observation_report``
    ≈ 18–52 agent wakeups. Each wakeup carries world state + diff, not just
    ``present/absent``.
    """

    def __init__(
        self,
        active_interval_sec: float = 1800,     # 30 min
        inactive_interval_sec: float = 7200,    # 2 hours
        inactive_jitter_sec: float = 3600,       # +0–1 h random
    ) -> None:
        """
        Args:
            active_interval_sec: Seconds between scheduled reports when present.
            inactive_interval_sec: Seconds between scheduled reports when absent.
            inactive_jitter_sec: Random jitter added to inactive interval (0–max).
        """
        self._active_interval = active_interval_sec
        self._inactive_interval = inactive_interval_sec
        self._inactive_jitter = inactive_jitter_sec
        self._lock = threading.Lock()

        # Presence tracking for absent duration
        self._absence_start: Optional[float] = None  # epoch seconds
        self._was_present: bool = False

        # Scheduled trigger state (wall-clock based)
        self._last_wake: Optional[str] = None        # ISO timestamp
        self._last_world_state: Optional[dict] = None
        self._last_scheduled_epoch: float = 0.0      # wall-clock time of last scheduled fire
        self._current_jitter: float = 0.0

        # 5-second dedup
        self._last_trigger_epoch: float = 0.0
        self._DEDUP_SEC: float = 5.0

    # ── Presence change tracking ──────────────────────────────────────

    def on_presence_change(self, present: bool) -> None:
        """Track presence transitions for absent duration calculation.

        Call this whenever V1's presence state changes.

        Args:
            present: True if someone is now present, False if absent.
        """
        with self._lock:
            if present and not self._was_present:
                # Transition: absent → present
                self._was_present = True
                # absence_start is kept so we can compute how long they were gone
            elif not present and self._was_present:
                # Transition: present → absent — start the absence timer
                self._was_present = False
                self._absence_start = time.time()
            # If state hasn't changed, do nothing

    def get_absent_duration(self) -> tuple:
        """Return (formatted_string, seconds) for current absence.

        Returns:
            (str, float | None) — e.g. ("5m 30s", 330.0) or ("unknown", None)
        """
        with self._lock:
            if self._absence_start is None:
                return ("unknown", None)
            elapsed = time.time() - self._absence_start
            return (_format_duration(elapsed), round(elapsed, 1))

    # ── Trigger A: presence_settled ─────────────────────────────────────

    def on_presence_settled(self, world_state: dict) -> Optional[TriggerEvent]:
        """Trigger A: presence_settled — someone arrived and is stable.

        Call this when V1's presence debounce confirms someone is present
        (the existing ``presence_settled`` logic in snarling.py).

        Args:
            world_state: Current world state snapshot.

        Returns:
            A :class:`TriggerEvent` if the trigger fires, or ``None`` if
            suppressed by dedup.
        """
        with self._lock:
            now_epoch = time.time()

            # 5-second dedup
            if now_epoch - self._last_trigger_epoch < self._DEDUP_SEC:
                return None

            # Compute absent duration
            absent_str, absent_sec = self._get_absent_duration_locked()

            changes = self._compute_changes_locked(world_state)

            event = TriggerEvent(
                trigger_reason="presence_settled",
                present=True,
                absent_duration=absent_str,
                absent_duration_sec=absent_sec,
                world_state=world_state,
                changes_since_last=changes,
                timestamp=_now_iso(),
            )

            self._last_wake = event.timestamp
            self._last_world_state = world_state
            self._last_trigger_epoch = now_epoch
            self._last_scheduled_epoch = now_epoch  # reset scheduled timer too

            # Reset absence timer (they're present now)
            self._absence_start = None

            return event

    # ── Trigger B: scheduled observation_report ──────────────────────────

    def on_scheduled(self, world_state: dict, presence_active: bool) -> Optional[TriggerEvent]:
        """Trigger B: periodic scheduled observation.

        Uses **wall-clock time** to determine when to fire, not frame counting.
        Call this whenever you want to check — it will only fire when enough
        time has elapsed since the last trigger.

        Active interval (present): every 30 min.
        Inactive interval (absent): every 2–4 h.

        The inactive interval includes random jitter to avoid exact-period
        artifacts.

        Args:
            world_state: Current world state snapshot.
            presence_active: True if someone is currently present.

        Returns:
            A :class:`TriggerEvent` if the scheduled interval has elapsed,
            or ``None`` if it's not time yet.
        """
        with self._lock:
            now_epoch = time.time()

            # Bootstrap: first call always emits an observation_report
            if self._last_wake is None:
                changes = self._compute_changes_locked(world_state)
                event = TriggerEvent(
                    trigger_reason="observation_report",
                    present=presence_active,
                    absent_duration="never",
                    absent_duration_sec=None,
                    world_state=world_state,
                    changes_since_last=changes,
                    timestamp=_now_iso(),
                )
                self._last_wake = event.timestamp
                self._last_world_state = world_state
                self._last_trigger_epoch = now_epoch
                self._last_scheduled_epoch = now_epoch
                self._current_jitter = random.uniform(0, self._inactive_jitter)
                return event

            # Determine interval based on presence
            if presence_active:
                interval_sec = self._active_interval
                jitter_sec = 0.0
            else:
                interval_sec = self._inactive_interval
                jitter_sec = self._current_jitter

            # Check if enough wall-clock time has elapsed
            elapsed = now_epoch - self._last_scheduled_epoch
            if elapsed < (interval_sec + jitter_sec):
                return None

            # 5-second dedup
            if now_epoch - self._last_trigger_epoch < self._DEDUP_SEC:
                return None

            # Interval elapsed — fire scheduled trigger
            absent_str, absent_sec = self._get_absent_duration_locked()
            changes = self._compute_changes_locked(world_state)

            event = TriggerEvent(
                trigger_reason="observation_report",
                present=presence_active,
                absent_duration=absent_str,
                absent_duration_sec=absent_sec,
                world_state=world_state,
                changes_since_last=changes,
                timestamp=_now_iso(),
            )

            self._last_wake = event.timestamp
            self._last_world_state = world_state
            self._last_trigger_epoch = now_epoch
            self._last_scheduled_epoch = now_epoch
            self._current_jitter = random.uniform(0, self._inactive_jitter)

            return event

    # ── Internal (must be called with lock held) ──────────────────────

    def _get_absent_duration_locked(self) -> tuple:
        """Compute absent duration. Lock must be held."""
        if self._absence_start is None:
            return ("unknown", None)
        elapsed = time.time() - self._absence_start
        # Only report absent_duration_sec for absences >= 60 s to avoid noise
        if elapsed < 60.0:
            return (f"{elapsed:.0f}s", None)
        return (_format_duration(elapsed), round(elapsed, 1))

    def _compute_changes_locked(self, current: dict) -> dict:
        """Diff current world state against last-sent state.

        If there's no previous state (bootstrap), returns
        ``{"bootstrap": True, ...current state...}``.
        """
        if self._last_world_state is None:
            result = {"bootstrap": True}
            result.update(current)
            return result

        prev = self._last_world_state
        current_sources = current.get("sources", {})
        prev_sources = prev.get("sources", {})

        changes: dict = {"source_count": current.get("source_count", 0)}

        # New sources (appeared)
        appeared = {}
        for sid, src in current_sources.items():
            if sid not in prev_sources:
                appeared[sid] = src
        if appeared:
            changes["appeared"] = appeared

        # Removed sources (disappeared)
        disappeared = {}
        for sid, src in prev_sources.items():
            if sid not in current_sources:
                disappeared[sid] = {
                    "last_seen": src.get("last_seen"),
                    "peak_temp": src.get("peak_temp"),
                    "observation_count": src.get("observation_count"),
                }
        if disappeared:
            changes["disappeared"] = disappeared

        # Changed sources (measurement delta > threshold)
        changed = {}
        for sid, curr_src in current_sources.items():
            if sid not in prev_sources:
                continue
            prev_src = prev_sources[sid]
            src_changes = {}
            for field_name in ("peak_temp", "centroid_drift", "temp_delta_10min"):
                curr_val = curr_src.get(field_name)
                prev_val = prev_src.get(field_name)
                if curr_val is None or prev_val is None:
                    continue
                if abs(curr_val - prev_val) > 0.5:
                    src_changes[field_name] = {"old": prev_val, "new": curr_val}
            if src_changes:
                changed[sid] = src_changes
        if changed:
            changes["changed"] = changed

        return changes