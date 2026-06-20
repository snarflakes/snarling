"""
world_state.py — Persistent source tracking with lifecycle.

The :class:`WorldState` accumulates thermal events in-process. Sources appear,
are tracked, and age out when they disappear for too long.  This is the
``thermal_v2`` equivalent of a database — condensed measurements, not raw
thermal history.

**Three-layer architecture:**

1. **Internal World State** (:meth:`get_snapshot`) — everything the tracker
   knows. Full source data including histories, classification, background
   confidence. Used for internal diff computation and persistence. ~1KB per
   source. Not intended for LLM consumption.

2. **Agent Context State** (:meth:`get_agent_context`) — distilled view for
   the environmental agent. No histories, no arrays, no raw shape traces.
   Only the measurements the agent actually reasons about: age, temperature,
   drift, shape stability. ~200 bytes per source. This is what goes in the
   observation report.

3. **Behavioral Summary** (written by the environmental agent to
   presence.db) — semantic labels like ``persistent_heat_source`` or
   ``mobile_thermal_phenomenon``. The main agent never sees thermal data,
   only behavioral conclusions.

Thread-safe: all mutations go through a :class:`threading.Lock`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class Source:
    """A tracked thermal source (person, appliance, etc.).

    The ``classification`` and ``background_confidence`` fields are reserved
    for Phase 2 three-state classification. In Phase 1 they're populated but
    not used for decision-making.

    Shape fields (width, height, area_pixels, aspect_ratio, and their
    histories) are raw measurements, not interpretations. Derived metrics
    like aspect_ratio_variance are computed from history, never stored.
    """

    id: str                         # e.g. "s_1"
    first_seen: str                  # ISO 8601 timestamp
    last_seen: str                   # ISO 8601 timestamp
    observation_count: int
    peak_temp: float                 # °C
    temp_range: tuple                # (min, max) °C
    temp_delta_10min: Optional[float]  # °C change over last 10 min, or None
    centroid_drift: float            # px/frame
    age_sec: float                   # seconds since first_seen
    # Shape measurements — raw, not interpreted
    width: float = 0.0              # bounding box width in pixels
    height: float = 0.0             # bounding box height in pixels
    area_pixels: int = 0           # pixel count from flood fill
    aspect_ratio: float = 1.0      # width / height — raw measurement
    aspect_ratio_history: list = field(default_factory=list)  # last N aspect_ratios
    width_history: list = field(default_factory=list)          # last N widths
    height_history: list = field(default_factory=list)         # last N heights
    classification: str = "uncertain"          # Phase 2: confident_present / confident_absent / uncertain
    background_confidence: float = 0.0         # Phase 2: 0.0–1.0


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class WorldState:
    """Persistent source tracking with lifecycle.

    Call :meth:`update` every frame with the current measurements.
    Internally tracks absent counters for aging sources out.

    Sources that are absent for more than ``max_absent_frames`` are removed
    (default 600 frames ≈ 2.5 min at 4 Hz, generous for brief occlusions).
    """

    def __init__(self, max_absent_frames: int = 600) -> None:
        """
        Args:
            max_absent_frames: Number of consecutive absent frames before a
                source is removed from tracking. Default 600 ≈ 2.5 min at 4 Hz.
        """
        self._lock = threading.Lock()
        self._sources: Dict[str, Source] = {}
        self._absent_counters: Dict[str, int] = {}
        self._max_absent_frames = max_absent_frames

    def update(self, measurements: list) -> dict:
        """Update world state with current measurements.

        Call this every frame. Sources present in ``measurements`` have their
        data updated and absent counters reset. Sources *not* in measurements
        have their absent counter incremented. Sources past
        ``max_absent_frames`` are removed.

        Args:
            measurements: List of :class:`~thermal_v2.measurements.SourceMeasurements`.

        Returns:
            dict — a snapshot of the current world state with keys
            ``source_count`` and ``sources``.
        """
        with self._lock:
            # Reset absent counters for sources seen this frame
            seen_ids: set = set()
            for m in measurements:
                seen_ids.add(m.source_id)

                if m.source_id in self._sources:
                    # Update existing source
                    src = self._sources[m.source_id]
                    src.last_seen = _now_iso()
                    src.observation_count = m.observation_count
                    src.peak_temp = m.peak_temp
                    src.temp_range = m.temp_range
                    src.temp_delta_10min = m.temp_delta_10min
                    src.centroid_drift = m.centroid_drift_px_per_frame
                    src.age_sec = m.age_sec
                    # Update shape measurements
                    src.width = m.width
                    src.height = m.height
                    src.area_pixels = m.area_pixels
                    src.aspect_ratio = m.aspect_ratio
                    src.aspect_ratio_history = m.aspect_ratio_history
                    src.width_history = m.width_history
                    src.height_history = m.height_history
                    # Reset absent counter
                    self._absent_counters[m.source_id] = 0
                else:
                    # New source appeared
                    self._sources[m.source_id] = Source(
                        id=m.source_id,
                        first_seen=_now_iso(),
                        last_seen=_now_iso(),
                        observation_count=m.observation_count,
                        peak_temp=m.peak_temp,
                        temp_range=m.temp_range,
                        temp_delta_10min=m.temp_delta_10min,
                        centroid_drift=m.centroid_drift_px_per_frame,
                        age_sec=m.age_sec,
                        width=m.width,
                        height=m.height,
                        area_pixels=m.area_pixels,
                        aspect_ratio=m.aspect_ratio,
                        aspect_ratio_history=m.aspect_ratio_history,
                        width_history=m.width_history,
                        height_history=m.height_history,
                    )
                    self._absent_counters[m.source_id] = 0

            # Increment absent counters for sources NOT seen this frame
            for sid in list(self._absent_counters):
                if sid not in seen_ids:
                    self._absent_counters[sid] = self._absent_counters.get(sid, 0) + 1

            # Remove sources past max_absent_frames
            to_remove = [
                sid
                for sid, count in self._absent_counters.items()
                if count > self._max_absent_frames
            ]
            for sid in to_remove:
                self._sources.pop(sid, None)
                del self._absent_counters[sid]

            return self._snapshot_unsafe()

    def get_snapshot(self) -> dict:
        """Return a full world state snapshot (internal use).

        Contains all tracking data including histories. Used by the tracker
        and for internal diff computation. Not intended for LLM consumption —
        use :meth:`get_agent_context` for that.

        Thread-safe.
        """
        with self._lock:
            return self._snapshot_unsafe()

    def get_agent_context(self, trigger_reason: str = "scheduled") -> dict:
        """Return distilled state for the environmental agent.

        This is the "Agent Context State" — the middle layer between the
        raw world state (everything) and the behavioral summary that goes
        to presence.db. It contains only the measurements the agent
        actually reasons about: no histories, no arrays, no internal
        tracking details.

        Sources with low observation_count (< 3) are excluded — they're
        too new to be meaningful. Sources are sorted by age (oldest first)
        so the most established sources appear first.

        Returns something like::

            {
                "trigger_reason": "scheduled",
                "summary": {
                    "source_count": 17,
                    "new_sources": 2,
                    "gone_sources": 1
                },
                "attention_sources": [
                    {
                        "id": "s_57",
                        "age_sec": 5400,
                        "peak_temp": 31.2,
                        "centroid_drift": 0.01,
                        "observation_count": 200,
                        "temp_delta_10min": 0.3,
                        "shape_stability": 0.95
                    }
                ]
            }
        """
        with self._lock:
            return self._agent_context_unsafe(trigger_reason)

    # ── Internal (must be called with lock held) ───────────────────

    def get_changes_since(self, last_snapshot: dict) -> dict:
        """Compute what changed since the last snapshot.

        Compares source IDs and measurement fields. A source is
        "changed" if its ``peak_temp`` delta > 0.5 °C or
        ``centroid_drift`` delta > 0.5 px/frame.

        Args:
            last_snapshot: A previous snapshot dict (from :meth:`get_snapshot`
                or :meth:`update`).

        Returns:
            dict with keys:
            - ``source_count``: current source count
            - ``appeared``: dict of new sources (source_id → source dict)
            - ``disappeared``: dict of gone sources (source_id → summary)
            - ``changed``: dict of changed sources (source_id → field changes)
        """
        with self._lock:
            return self._changes_since_unsafe(last_snapshot)

    # ── Internal (must be called with lock held) ───────────────────

    def _snapshot_unsafe(self) -> dict:
        """Build a snapshot dict without acquiring the lock."""
        return {
            "source_count": len(self._sources),
            "sources": {
                sid: {
                    "id": src.id,
                    "first_seen": src.first_seen,
                    "last_seen": src.last_seen,
                    "observation_count": src.observation_count,
                    "peak_temp": src.peak_temp,
                    "temp_range": list(src.temp_range),  # list for JSON
                    "temp_delta_10min": src.temp_delta_10min,
                    "centroid_drift": src.centroid_drift,
                    "age_sec": src.age_sec,
                    "width": src.width,
                    "height": src.height,
                    "area_pixels": src.area_pixels,
                    "aspect_ratio": src.aspect_ratio,
                    "aspect_ratio_history": src.aspect_ratio_history[-30:],  # last ~7.5s
                    "width_history": src.width_history[-30:],
                    "height_history": src.height_history[-30:],
                    "classification": src.classification,
                    "background_confidence": src.background_confidence,
                }
                for sid, src in self._sources.items()
            },
        }

    def _agent_context_unsafe(self, trigger_reason: str) -> dict:
        """Build distilled agent context without acquiring the lock."""
        # Count established vs new sources
        established = [
            src for src in self._sources.values()
            if src.observation_count >= 3
        ]
        new_count = sum(
            1 for src in self._sources.values()
            if src.observation_count < 3
        )

        # Sort established sources by age (oldest first — most meaningful)
        established.sort(key=lambda s: s.age_sec, reverse=True)

        # Build attention_sources — only established, only distilled fields
        attention_sources = []
        for src in established:
            entry = {
                "id": src.id,
                "age_sec": round(src.age_sec, 1),
                "peak_temp": round(src.peak_temp, 1),
                "centroid_drift": round(src.centroid_drift, 3),
                "observation_count": src.observation_count,
            }
            if src.temp_delta_10min is not None:
                entry["temp_delta_10min"] = round(src.temp_delta_10min, 2)

            # Shape stability: how consistent is the aspect ratio?
            # Computed from history, not stored — this is the one derived metric
            # that's useful for the agent. 1.0 = rock-solid shape, 0.0 = chaotic.
            if len(src.aspect_ratio_history) >= 3:
                recent = src.aspect_ratio_history[-10:]
                mean_ar = sum(recent) / len(recent)
                variance = sum((x - mean_ar) ** 2 for x in recent) / len(recent)
                # Normalize: variance of 0 → stability 1.0, variance of 0.5+ → stability 0.0
                stability = max(0.0, min(1.0, 1.0 - variance / 0.5))
                entry["shape_stability"] = round(stability, 2)

            attention_sources.append(entry)

        context = {
            "trigger_reason": trigger_reason,
            "summary": {
                "source_count": len(self._sources),
                "established_sources": len(established),
                "new_sources": new_count,
            },
        }
        if attention_sources:
            context["attention_sources"] = attention_sources

        return context

    def _changes_since_unsafe(self, last_snapshot: dict) -> dict:
        """Compute diff against a previous snapshot (lock must be held)."""
        current = self._snapshot_unsafe()
        current_sources = current.get("sources", {})
        prev_sources = last_snapshot.get("sources", {})

        changes: dict = {"source_count": current["source_count"]}

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
                continue  # already in "appeared"
            prev_src = prev_sources[sid]
            src_changes = {}
            # Check significant measurement changes
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