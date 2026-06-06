"""
tracker.py — Frame-to-frame blob correlation with stable IDs.

Receives raw blob dicts from V1's flood-fill detection and correlates them
across frames using centroid proximity. Each new blob that can't be matched
gets a fresh ID; matched blobs keep their ID for their entire lifetime.

Blobs that disappear are aged out after ``max_absent_frames`` consecutive
frames without a match (~1.5 s at 4 Hz).

This module is pure Python with no external dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TrackedBlob:
    """A blob tracked across frames with a stable ID."""

    id: str                     # e.g. "s_1", "s_2" — stable while blob persists
    centroid: tuple             # (row, col) centre of mass
    pixel_count: int            # number of pixels in the blob
    temp_min: float             # min temperature in the blob (°C)
    temp_max: float             # max temperature in the blob (°C)
    temp_mean: float            # mean temperature in the blob (°C)
    first_frame: int            # frame number when this blob first appeared
    last_frame: int             # frame number when this blob was last seen
    absent_count: int = 0       # consecutive frames this blob was not matched


def _centroid_distance(a: tuple, b: tuple) -> float:
    """Euclidean distance between two (row, col) centroids."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


class BlobTracker:
    """Frame-to-frame blob correlation using centroid proximity.

    Each call to :meth:`update` receives the blobs detected in the current
    frame and returns the list of currently tracked blobs (including ones
    that were seen in a previous frame but not in this one, with an updated
    ``absent_count``).

    Blobs that exceed ``max_absent_frames`` are removed from tracking.
    """

    def __init__(
        self,
        max_drift_px: float = 5.0,
        max_absent_frames: int = 6,
    ) -> None:
        """
        Args:
            max_drift_px: Maximum centroid distance (pixels) to consider
                a new blob the same as an existing tracked blob.
            max_absent_frames: Number of consecutive frames a blob can
                be absent before it's removed from tracking. Default 6
                (~1.5 s at 4 Hz).
        """
        self._max_drift_px = max_drift_px
        self._max_absent_frames = max_absent_frames
        self._tracked: Dict[str, TrackedBlob] = {}
        self._next_id: int = 1
        self._frame: int = 0

    def update(self, frame_blobs: List[dict]) -> List[TrackedBlob]:
        """Correlate new frame blobs with existing tracked blobs.

        Args:
            frame_blobs: List of dicts, each with keys:
                ``centroid`` (row, col), ``pixel_count``, ``temp_min``,
                ``temp_max``, ``temp_mean``.

        Returns:
            List of all currently tracked :class:`TrackedBlob` objects.
            Blobs that were not matched this frame have ``absent_count``
            incremented. Blobs past ``max_absent_frames`` are removed.
        """
        self._frame += 1

        # --- Match new blobs to existing tracked blobs -------------------
        matched_tracked_ids: set = set()
        matched_new_indices: set = set()

        # Build a simple distance matrix (O(n*m), n/m typically 1-5)
        assignments: List[tuple] = []  # (distance, tracked_id, new_idx)
        for new_idx, new_blob in enumerate(frame_blobs):
            for tid, tracked in self._tracked.items():
                dist = _centroid_distance(new_blob["centroid"], tracked.centroid)
                if dist <= self._max_drift_px:
                    assignments.append((dist, tid, new_idx))

        # Greedy assignment: closest first
        assignments.sort()
        for dist, tid, new_idx in assignments:
            if tid in matched_tracked_ids or new_idx in matched_new_indices:
                continue
            # Update the tracked blob with new frame data
            tracked = self._tracked[tid]
            new_blob = frame_blobs[new_idx]
            tracked.centroid = new_blob["centroid"]
            tracked.pixel_count = new_blob["pixel_count"]
            tracked.temp_min = new_blob["temp_min"]
            tracked.temp_max = new_blob["temp_max"]
            tracked.temp_mean = new_blob["temp_mean"]
            tracked.last_frame = self._frame
            tracked.absent_count = 0
            matched_tracked_ids.add(tid)
            matched_new_indices.add(new_idx)

        # --- Create new tracked blobs for unmatched new blobs ------------
        for new_idx, new_blob in enumerate(frame_blobs):
            if new_idx in matched_new_indices:
                continue
            sid = f"s_{self._next_id}"
            self._next_id += 1
            tb = TrackedBlob(
                id=sid,
                centroid=new_blob["centroid"],
                pixel_count=new_blob["pixel_count"],
                temp_min=new_blob["temp_min"],
                temp_max=new_blob["temp_max"],
                temp_mean=new_blob["temp_mean"],
                first_frame=self._frame,
                last_frame=self._frame,
                absent_count=0,
            )
            self._tracked[sid] = tb

        # --- Age out absent blobs -----------------------------------------
        for tid in list(self._tracked):
            if tid in matched_tracked_ids:
                continue
            self._tracked[tid].absent_count += 1
            self._tracked[tid].last_frame = self._frame  # still increment frame counter

        # Remove blobs that exceeded max_absent_frames
        to_remove = [
            tid
            for tid, tb in self._tracked.items()
            if tb.absent_count > self._max_absent_frames
        ]
        for tid in to_remove:
            del self._tracked[tid]

        # Return a snapshot of all currently tracked blobs
        return list(self._tracked.values())

    @property
    def frame(self) -> int:
        """Current frame counter (incremented each call to update)."""
        return self._frame