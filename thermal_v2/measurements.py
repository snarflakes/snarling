"""
measurements.py — Extract condensed measurements from tracked blobs.

Each tracked blob (a "source") accumulates a temperature history so we can
compute drift, age, and delta-over-time.  Measurements are **numbers only** —
no labels, no classification.  That's the agent's job.

Temperature history is kept in a per-source deque (max 2 400 entries = 10 min
at 4 Hz).  This is enough to compute a 10-minute temperature delta without
growing unbounded.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class SourceMeasurements:
    """Condensed measurements for one source (tracked blob)."""

    source_id: str                          # e.g. "s_1"
    peak_temp: float                        # max temperature observed (°C)
    temp_range: Tuple[float, float]         # (min, max) temperature range (°C)
    temp_delta_10min: Optional[float]       # temperature change over last 10 min (°C), or None
    centroid_drift_px_per_frame: float       # average centroid movement between frames (px/frame)
    age_sec: float                          # seconds since first observation
    observation_count: int                  # number of frames this source has been seen


# Time constant: 4 Hz frame rate
_FRAME_INTERVAL_SEC: float = 0.25


class MeasurementExtractor:
    """Computes measurements from tracked blobs.

    Maintains a per-source temperature history (deque, max 2 400 entries =
    10 min at 4 Hz) and centroid history for drift computation.

    Usage::

        extractor = MeasurementExtractor()
        # each frame:
        measurements = extractor.extract(tracked_blobs)
    """

    def __init__(self, history_len: int = 2400) -> None:
        """
        Args:
            history_len: Max number of temperature history entries per source.
                Default 2 400 = 10 min at 4 Hz.
        """
        self._history_len = history_len
        # source_id → deque of (timestamp, temp_mean)
        self._temp_history: Dict[str, deque] = {}
        # source_id → list of recent centroids for drift computation
        self._centroid_history: Dict[str, deque] = {}
        # source_id → first_seen timestamp (epoch seconds)
        self._first_seen: Dict[str, float] = {}
        # source_id → total observation count
        self._obs_count: Dict[str, int] = {}

    def extract(self, tracked_blobs: list) -> List[SourceMeasurements]:
        """Extract measurements from the current frame's tracked blobs.

        Args:
            tracked_blobs: List of :class:`~thermal_v2.tracker.TrackedBlob`
                objects (from :meth:`BlobTracker.update`).

        Returns:
            List of :class:`SourceMeasurements`, one per tracked blob that
            was seen in this frame (``absent_count == 0``).
        """
        now = time.time()
        results: List[SourceMeasurements] = []

        for blob in tracked_blobs:
            # Only compute measurements for blobs seen this frame
            if blob.absent_count > 0:
                continue

            sid = blob.id

            # Initialise first_seen on first observation
            if sid not in self._first_seen:
                self._first_seen[sid] = now
                self._obs_count[sid] = 0

            # Increment observation count
            self._obs_count[sid] = self._obs_count.get(sid, 0) + 1

            # --- Temperature history --------------------------------------
            if sid not in self._temp_history:
                self._temp_history[sid] = deque(maxlen=self._history_len)
            self._temp_history[sid].append((now, blob.temp_mean))

            # --- Centroid history -----------------------------------------
            if sid not in self._centroid_history:
                self._centroid_history[sid] = deque(maxlen=30)  # ~7.5 s of centroid data
            self._centroid_history[sid].append(blob.centroid)

            # --- Compute measurements --------------------------------------
            peak_temp = blob.temp_max
            temp_range = (blob.temp_min, blob.temp_max)

            # Temperature delta over last 10 minutes
            temp_delta_10min: Optional[float] = None
            history = self._temp_history[sid]
            if len(history) >= 2:
                # Find the oldest entry within the last 10 minutes
                cutoff = now - 600.0  # 10 minutes ago
                oldest_in_window = None
                for ts, temp in history:
                    if ts >= cutoff:
                        if oldest_in_window is None:
                            oldest_in_window = (ts, temp)
                        break
                # If we didn't find one via the loop, scan from left
                if oldest_in_window is None:
                    for ts, temp in history:
                        if ts >= cutoff:
                            oldest_in_window = (ts, temp)
                            break
                # Fallback: use the first entry in the deque
                if oldest_in_window is None and len(history) > 0:
                    oldest_in_window = (history[0][0], history[0][1])

                if oldest_in_window is not None:
                    current_temp = blob.temp_mean
                    old_temp = oldest_in_window[1]
                    # Only report if we have at least 60 seconds of history
                    age_of_oldest = now - oldest_in_window[0]
                    if age_of_oldest >= 60.0:
                        temp_delta_10min = round(current_temp - old_temp, 2)

            # Centroid drift: average pixel distance between consecutive frames
            centroid_drift_px_per_frame = 0.0
            centroids = self._centroid_history[sid]
            if len(centroids) >= 2:
                total_drift = 0.0
                count = 0
                prev = centroids[0]
                for i in range(1, len(centroids)):
                    curr = centroids[i]
                    dr = curr[0] - prev[0]
                    dc = curr[1] - prev[1]
                    total_drift += (dr * dr + dc * dc) ** 0.5
                    count += 1
                    prev = curr
                if count > 0:
                    centroid_drift_px_per_frame = round(total_drift / count, 3)

            # Age in seconds
            age_sec = round(now - self._first_seen[sid], 1)

            results.append(SourceMeasurements(
                source_id=sid,
                peak_temp=round(peak_temp, 2),
                temp_range=(round(temp_range[0], 2), round(temp_range[1], 2)),
                temp_delta_10min=temp_delta_10min,
                centroid_drift_px_per_frame=centroid_drift_px_per_frame,
                age_sec=age_sec,
                observation_count=self._obs_count[sid],
            ))

        # Clean up sources that are no longer tracked
        # (tracked_blobs includes absent ones; remove those that aren't in the list at all)
        active_ids = {b.id for b in tracked_blobs}
        for sid in list(self._temp_history):
            if sid not in active_ids:
                del self._temp_history[sid]
                self._first_seen.pop(sid, None)
                self._obs_count.pop(sid, None)
        for sid in list(self._centroid_history):
            if sid not in active_ids:
                del self._centroid_history[sid]

        return results