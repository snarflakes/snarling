"""
tracker.py — Frame-to-frame blob correlation with stable IDs.

Receives raw blob dicts from V1's flood-fill detection and correlates them
across frames using centroid proximity. Each new blob that can't be matched
gets a fresh ID; matched blobs keep their ID for their entire lifetime.

Blobs that disappear are aged out after ``max_absent_frames`` consecutive
frames without a match (~1.5 s at 4 Hz).

Shape measurements (width, height, area_pixels, aspect_ratio) are stored
as raw observations and tracked over time. Derived metrics like
aspect_ratio_variance are computed from the history, never stored directly.

This module is pure Python with no external dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Maximum history length per blob (roughly 2.5 minutes at 4Hz)
MAX_HISTORY = 600


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
    absent_count: int = 0      # consecutive frames this blob was not matched
    # Shape measurements — raw, not interpreted
    bbox: tuple = (0, 0, 0, 0)  # (min_row, min_col, max_row, max_col)
    width: float = 0.0          # bounding box width in pixels
    height: float = 0.0         # bounding box height in pixels
    area_pixels: int = 0       # pixel count from flood fill
    aspect_ratio: float = 1.0  # width / height — raw measurement, not a shape type
    # History — raw measurements over time, derived metrics computed later
    centroid_history: list = field(default_factory=list)       # last N centroids
    temp_history: list = field(default_factory=list)           # last N temp_mean values
    aspect_ratio_history: list = field(default_factory=list)   # last N aspect_ratios
    width_history: list = field(default_factory=list)          # last N widths
    height_history: list = field(default_factory=list)         # last N heights


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
                ``temp_max``, ``temp_mean``, ``bbox``, ``width``,
                ``height``, ``area_pixels``, ``aspect_ratio``.

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
            # Update shape measurements
            tracked.bbox = new_blob.get("bbox", tracked.bbox)
            tracked.width = new_blob.get("width", tracked.width)
            tracked.height = new_blob.get("height", tracked.height)
            tracked.area_pixels = new_blob.get("area_pixels", tracked.area_pixels)
            tracked.aspect_ratio = new_blob.get("aspect_ratio", tracked.aspect_ratio)
            # Append to histories (capped at MAX_HISTORY)
            tracked.centroid_history.append(tracked.centroid)
            tracked.temp_history.append(tracked.temp_mean)
            tracked.aspect_ratio_history.append(tracked.aspect_ratio)
            tracked.width_history.append(tracked.width)
            tracked.height_history.append(tracked.height)
            if len(tracked.centroid_history) > MAX_HISTORY:
                tracked.centroid_history = tracked.centroid_history[-MAX_HISTORY:]
            if len(tracked.temp_history) > MAX_HISTORY:
                tracked.temp_history = tracked.temp_history[-MAX_HISTORY:]
            if len(tracked.aspect_ratio_history) > MAX_HISTORY:
                tracked.aspect_ratio_history = tracked.aspect_ratio_history[-MAX_HISTORY:]
            if len(tracked.width_history) > MAX_HISTORY:
                tracked.width_history = tracked.width_history[-MAX_HISTORY:]
            if len(tracked.height_history) > MAX_HISTORY:
                tracked.height_history = tracked.height_history[-MAX_HISTORY:]
            matched_tracked_ids.add(tid)
            matched_new_indices.add(new_idx)

        # --- Create new tracked blobs for unmatched new blobs ------------
        new_blob_ids: set = set()  # IDs created this frame (not absent)
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
                bbox=new_blob.get("bbox", (0, 0, 0, 0)),
                width=new_blob.get("width", 0.0),
                height=new_blob.get("height", 0.0),
                area_pixels=new_blob.get("area_pixels", new_blob["pixel_count"]),
                aspect_ratio=new_blob.get("aspect_ratio", 1.0),
                centroid_history=[new_blob["centroid"]],
                temp_history=[new_blob["temp_mean"]],
                aspect_ratio_history=[new_blob.get("aspect_ratio", 1.0)],
                width_history=[new_blob.get("width", 0.0)],
                height_history=[new_blob.get("height", 0.0)],
            )
            self._tracked[sid] = tb
            new_blob_ids.add(sid)

        # --- Age out absent blobs -----------------------------------------
        # Only increment absent_count for blobs that existed before this frame
        # and were not matched. Newly created blobs are present, not absent.
        for tid in list(self._tracked):
            if tid in matched_tracked_ids or tid in new_blob_ids:
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