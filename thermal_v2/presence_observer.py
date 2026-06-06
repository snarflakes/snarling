"""
presence_observer.py — Presence detection from world state + V1 sensor.

In Phase 1, this wraps V1's binary presence detection from
:class:`ThermalSensor`. It interprets the world state alongside the raw
``present`` / ``proximity`` signals to produce a richer presence reading.

Phase 2 will add three-state classification (confident_present /
confident_absent / uncertain) with background confidence scoring.
"""

from __future__ import annotations

from typing import Dict, Optional


class PresenceObserver:
    """Interpret world state + V1 sensor data for presence.

    This class is stateless — it doesn't maintain history. It takes the
    current world state snapshot and V1 sensor properties and produces a
    presence reading.

    Usage::

        observer = PresenceObserver()
        result = observer.check(
            world_state=ws.get_snapshot(),
            v1_present=sensor.present,
            v1_proximity=sensor.proximity,
            v1_zone=sensor.get_presence_info()["proximity_zone"],
        )
    """

    def check(
        self,
        world_state: dict,
        v1_present: bool = False,
        v1_proximity: float = 0.0,
        v1_zone: str = "absent",
    ) -> dict:
        """Check presence from world state and V1 sensor data.

        Args:
            world_state: A world state snapshot (from
                :meth:`WorldState.get_snapshot`).
            v1_present: V1's binary present/absent from ThermalSensor.
            v1_proximity: V1's proximity score (0.0–1.0).
            v1_zone: V1's proximity zone string.

        Returns:
            dict with keys:
            - ``present`` (bool): whether someone is present
            - ``confidence`` (float): 0.0–1.0 confidence
            - ``proximity_zone`` (str): "absent", "approaching", or "present"
        """
        source_count = world_state.get("source_count", 0)
        sources = world_state.get("sources", {})

        # Phase 1: confidence is a simple heuristic combining V1 presence
        # with world state richness.
        # More sources and more observations → higher confidence.
        # Minimum 0.3 if V1 says present (we trust V1's blob detection).
        # Capped at 1.0.
        max_observations = 0
        for src in sources.values():
            obs = src.get("observation_count", 0)
            if obs > max_observations:
                max_observations = obs

        confidence = 0.0
        if v1_present:
            confidence = min(1.0, 0.3 + 0.1 * source_count + 0.05 * min(max_observations / 100, 10))

        return {
            "present": v1_present,
            "confidence": round(confidence, 3),
            "proximity_zone": v1_zone,
        }