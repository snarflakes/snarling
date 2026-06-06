"""
thermal_v2 — V2 thermal presence pipeline for snarling.

Adds blob tracking, source lifecycle, world state accumulation, and a trigger
scheduler that only wakes the agent on ``presence_settled`` or ``scheduled``
events with rich payloads (world state snapshot + changes-since-last diff).

Phase 1 keeps V1's flood-fill blob detection as Layer 1 and layers tracking,
measurement extraction, and world state on top.

Pure Python — no numpy, no opencv, no external dependencies.
"""

from .tracker import BlobTracker, TrackedBlob
from .measurements import MeasurementExtractor, SourceMeasurements
from .world_state import WorldState, Source
from .presence_observer import PresenceObserver
from .trigger_scheduler import TriggerScheduler, TriggerEvent

__all__ = [
    "BlobTracker",
    "TrackedBlob",
    "MeasurementExtractor",
    "SourceMeasurements",
    "WorldState",
    "Source",
    "PresenceObserver",
    "TriggerScheduler",
    "TriggerEvent",
]