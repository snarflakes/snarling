"""
snarling Middleware - Lightweight status updater for OpenClaw mission-control API.

Thin wrapper around HTTP status updates. No persistent state, minimal dependencies.

Usage:
    from snarling_middleware import set_processing, set_idle
    
    set_processing()  # Before LLM call
    # ... do work ...
    set_idle()        # After done
"""

import os
import requests
from datetime import datetime


API_URL = os.environ.get("snarling_API_URL", "http://localhost:3000/api/status")


def _update_status(state: str) -> bool:
    """
    POST state update to the mission-control API.
    
    Args:
        state: One of "processing", "responding", "idle", "error"
    
    Returns:
        True if successful, False otherwise (fail silently)
    """
    try:
        print(f"[{datetime.now()}] Middleware: POST {state} to {API_URL}")
        response = requests.post(
            API_URL,
            json={"status": state},
            timeout=2
        )
        success = response.status_code == 200
        if not success:
            print(f"[{datetime.now()}] Middleware: POST failed with status {response.status_code}")
        return success
    except Exception as e:
        print(f"[{datetime.now()}] Middleware: POST failed with exception: {e}")
        # Fail silently - this is a nice-to-have, not critical
        return False


def set_processing() -> bool:
    """Set snarling state to 'processing' (e.g., before LLM call)."""
    return _update_status("processing")


def set_responding() -> bool:
    """Set snarling state to 'responding' (e.g., when generating response)."""
    return _update_status("responding")


def set_idle() -> bool:
    """Set snarling state to 'idle' (e.g., after work is done)."""
    return _update_status("idle")


def set_error() -> bool:
    """Set snarling state to 'error' (e.g., when something goes wrong)."""
    return _update_status("error")


# Backwards-compatible naming for import
def set_busy() -> bool:
    """Alias for set_processing()."""
    return set_processing()
