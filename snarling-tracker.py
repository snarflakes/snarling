"""
snarling Tracker - State time tracker with health score calculation.

Tracks cumulative time in each state and calculates a health score based on
activity patterns. Saves to JSON on exit, loads on start.

Future gamification ideas:
- Health score decays if idle too long
- Gain points for responsiveness and uptime
- Activity streaks and achievements

Usage:
    from snarling_tracker import snarlingTracker
    
    tracker = snarlingTracker()
    tracker.start("processing")
    # ... do work ...
    tracker.transition("idle")
    stats = tracker.get_stats()
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict


DEFAULT_STATE_FILE = os.path.expanduser("~/.openclaw/snarling-state.json")


@dataclass
class snarlingState:
    """Persistent state for the snarling tracker."""
    # Time counters (in seconds)
    time_in_processing: float = 0.0
    time_in_responding: float = 0.0
    time_in_idle: float = 0.0
    time_in_error: float = 0.0
    
    # Health metrics
    total_uptime: float = 0.0
    responsiveness_score: float = 100.0  # 0-100
    health_score: float = 100.0  # 0-100
    
    # Activity tracking
    state_changes: int = 0
    last_activity: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class snarlingTracker:
    """
    Tracks time spent in each snarling state and calculates health metrics.
    
    Designed for future gamification features like health decay, responsiveness
    bonuses, and activity streaks.
    """
    
    VALID_STATES = {"processing", "responding", "idle", "error"}
    
    def __init__(self, state_file: str = DEFAULT_STATE_FILE):
        self.state_file = state_file
        self._state = self._load_state()
        self._current_state: Optional[str] = None
        self._state_start_time: Optional[float] = None
        self._initialized_at = time.time()
        
        # Start in idle
        self.transition("idle")
    
    def _load_state(self) -> snarlingState:
        """Load persistent state from JSON file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                return snarlingState(**data)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass  # Fall through to create new state
        return snarlingState()
    
    def _save_state(self) -> bool:
        """Save current state to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            self._state.updated_at = time.time()
            with open(self.state_file, 'w') as f:
                json.dump(asdict(self._state), f, indent=2)
            return True
        except (OSError, IOError):
            return False
    
    def _update_time_in_state(self, state: str, duration: float) -> None:
        """Add duration to the appropriate state counter."""
        if state == "processing":
            self._state.time_in_processing += duration
        elif state == "responding":
            self._state.time_in_responding += duration
        elif state == "idle":
            self._state.time_in_idle += duration
        elif state == "error":
            self._state.time_in_error += duration
    
    def _calculate_health_score(self) -> float:
        """
        Calculate health score based on activity patterns.
        
        Factors:
        - More processing/responding = higher activity (good)
        - Too much idle = decay
        - Responsiveness = quick transitions
        """
        total_tracked = (
            self._state.time_in_processing +
            self._state.time_in_responding +
            self._state.time_in_idle +
            self._state.time_in_error
        )
        
        if total_tracked == 0:
            return 100.0
        
        # Activity ratio: active time / total time
        active_time = self._state.time_in_processing + self._state.time_in_responding
        activity_ratio = active_time / total_tracked
        
        # Health decays if less than 20% active time
        base_health = 50.0 + (activity_ratio * 50.0)
        
        # Boost for responsiveness score
        responsiveness_bonus = self._state.responsiveness_score * 0.2
        
        # Penalty for error time
        error_penalty = (self._state.time_in_error / max(total_tracked, 1)) * 20.0
        
        health = base_health + responsiveness_bonus - error_penalty
        return max(0.0, min(100.0, health))
    
    def transition(self, new_state: str) -> bool:
        """
        Transition to a new state, tracking time spent in previous state.
        
        Args:
            new_state: One of "processing", "responding", "idle", "error"
        
        Returns:
            True if transition was successful
        """
        if new_state not in self.VALID_STATES:
            return False
        
        now = time.time()
        
        # Update time spent in previous state
        if self._current_state and self._state_start_time:
            duration = now - self._state_start_time
            self._update_time_in_state(self._current_state, duration)
            self._state.total_uptime += duration
        
        # Update state change count and timestamps
        self._state.state_changes += 1
        self._state.last_activity = now
        
        # Calculate responsiveness bonus for quick transitions
        if self._current_state and self._state_start_time:
            transition_time = now - self._state_start_time
            if transition_time < 1.0:  # Less than 1 second
                self._state.responsiveness_score = min(
                    100.0, self._state.responsiveness_score + 1.0
                )
        
        # Update health score
        self._state.health_score = self._calculate_health_score()
        
        # Switch to new state
        self._current_state = new_state
        self._state_start_time = now
        
        # Save state
        self._save_state()
        
        return True
    
    def start(self, state: str) -> bool:
        """Alias for transition(), for clarity at initialization."""
        return self.transition(state)
    
    def get_current_state(self) -> Optional[str]:
        """Get the current state name."""
        return self._current_state
    
    def get_time_in_current_state(self) -> float:
        """Get seconds spent in current state so far."""
        if self._state_start_time is None:
            return 0.0
        return time.time() - self._state_start_time
    
    def get_stats(self) -> Dict:
        """
        Get current statistics as a dictionary.
        
        Returns:
            Dict with time counters, health metrics, and current state info
        """
        current_duration = self.get_time_in_current_state()
        
        return {
            "current_state": self._current_state,
            "time_in_current_state": current_duration,
            "cumulative_times": {
                "processing": self._state.time_in_processing,
                "responding": self._state.time_in_responding,
                "idle": self._state.time_in_idle,
                "error": self._state.time_in_error,
            },
            "total_tracked": (
                self._state.time_in_processing +
                self._state.time_in_responding +
                self._state.time_in_idle +
                self._state.time_in_error
            ),
            "health_score": round(self._state.health_score, 2),
            "responsiveness_score": round(self._state.responsiveness_score, 2),
            "state_changes": self._state.state_changes,
            "session_started": self._initialized_at,
        }
    
    def save(self) -> bool:
        """Explicitly save current state to file."""
        # Update current state duration before saving
        if self._current_state and self._state_start_time:
            duration = time.time() - self._state_start_time
            self._update_time_in_state(self._current_state, duration)
            self._state.total_uptime += duration
            self._state_start_time = time.time()  # Reset for continued tracking
        
        self._state.health_score = self._calculate_health_score()
        return self._save_state()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - saves state."""
        self.save()
    
    def __del__(self):
        """Destructor - attempts to save state on garbage collection."""
        try:
            self.save()
        except Exception:
            pass  # Best effort only


# Convenience functions for simple usage
_tracker: Optional[snarlingTracker] = None


def get_tracker() -> snarlingTracker:
    """Get or create the global tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = snarlingTracker()
    return _tracker


def transition(state: str) -> bool:
    """Global convenience function to transition state."""
    return get_tracker().transition(state)


def get_stats() -> Dict:
    """Global convenience function to get stats."""
    return get_tracker().get_stats()


def save() -> bool:
    """Global convenience function to save state."""
    return get_tracker().save()
