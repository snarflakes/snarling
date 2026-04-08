#!/usr/bin/env python3
"""
OpenClaw Integration Module for snarling

Uses Server-Sent Events (SSE) for real-time agent status updates,
with graceful fallback to polling if SSE is unavailable.
Runs in a background thread with graceful degradation.
"""

import threading
import time
import json
import logging
import requests
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Dict, Any, Iterator
from dataclasses import dataclass, field
from queue import Queue

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class snarlingState(Enum):
    """snarling states that map from OpenClaw agent states."""
    SLEEPING = "sleeping"
    PROCESSING = "processing"
    COMMUNICATING = "communicating"
    ERROR = "error"
    UNKNOWN = "unknown"


class OpenClawState(Enum):
    """Known OpenClaw agent states."""
    IDLE = "idle"
    PROCESSING = "processing"
    WORKING = "working"
    RESPONDING = "responding"
    SPEAKING = "speaking"
    ERROR = "error"
    UNKNOWN = "unknown"


# Mapping from OpenClaw states to snarling states
STATE_MAPPING = {
    OpenClawState.IDLE: snarlingState.SLEEPING,
    OpenClawState.PROCESSING: snarlingState.PROCESSING,
    OpenClawState.WORKING: snarlingState.PROCESSING,
    OpenClawState.RESPONDING: snarlingState.COMMUNICATING,
    OpenClawState.SPEAKING: snarlingState.COMMUNICATING,
    OpenClawState.ERROR: snarlingState.ERROR,
    OpenClawState.UNKNOWN: snarlingState.UNKNOWN,
}


@dataclass
class OpenClawStatus:
    """Represents the current OpenClaw status."""
    connected: bool = False
    raw_state: str = "unknown"
    snarling_state: snarlingState = snarlingState.UNKNOWN
    last_update: float = field(default_factory=time.time)
    error_message: Optional[str] = None
    session_id: Optional[str] = None
    agent_name: Optional[str] = None


class SSEClient:
    """
    Simple Server-Sent Events (SSE) client using requests.
    
    Parses SSE format:
        event: message
        data: {"status": "...", ...}
    """
    
    def __init__(self, url: str, timeout: float = 60.0, headers: Optional[Dict] = None):
        self.url = url
        self.timeout = timeout
        self.headers = headers or {}
        self.response: Optional[requests.Response] = None
        self._connected = False
        self._buffer = ""
    
    def connect(self) -> Iterator[Dict[str, Any]]:
        """
        Connect to SSE stream and yield parsed events.
        
        Yields:
            Dict containing event data (usually has 'data' key)
        """
        try:
            self.headers['Accept'] = 'text/event-stream'
            self.headers['Cache-Control'] = 'no-cache'
            
            self.response = requests.get(
                self.url,
                headers=self.headers,
                stream=True,
                timeout=self.timeout
            )
            self.response.raise_for_status()
            self._connected = True
            
            logger.info(f"SSE connected to {self.url}")
            
            for chunk in self.response.iter_content(chunk_size=1024, decode_unicode=True):
                if chunk is None or chunk == "":
                    continue
                
                self._buffer += chunk
                
                # Process complete events (separated by double newline)
                while '\n\n' in self._buffer:
                    event_block, self._buffer = self._buffer.split('\n\n', 1)
                    event = self._parse_event(event_block)
                    if event:
                        yield event
                        
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"SSE connection error: {e}")
            self._connected = False
            raise
        except requests.exceptions.HTTPError as e:
            logger.warning(f"SSE HTTP error: {e}")
            self._connected = False
            raise
        except Exception as e:
            logger.error(f"SSE unexpected error: {e}")
            self._connected = False
            raise
        finally:
            self._connected = False
            if self.response:
                self.response.close()
    
    def _parse_event(self, event_block: str) -> Optional[Dict[str, Any]]:
        """
        Parse an SSE event block into a dictionary.
        
        Args:
            event_block: Raw SSE event text (lines with event: and data:)
            
        Returns:
            Parsed event dict or None if invalid
        """
        event_data = {}
        data_lines = []
        
        for line in event_block.strip().split('\n'):
            if line.startswith('event:'):
                event_data['event'] = line[6:].strip()
            elif line.startswith('data:'):
                data_lines.append(line[5:].strip())
            elif line.startswith('id:'):
                event_data['id'] = line[3:].strip()
            elif line.startswith('retry:'):
                try:
                    event_data['retry'] = int(line[6:].strip())
                except ValueError:
                    pass
        
        if data_lines:
            full_data = '\n'.join(data_lines)
            try:
                event_data['data'] = json.loads(full_data)
            except json.JSONDecodeError:
                event_data['data'] = full_data
        
        return event_data if data_lines else None
    
    @property
    def connected(self) -> bool:
        """Check if connected to SSE stream."""
        return self._connected and self.response is not None
    
    def close(self):
        """Close the SSE connection."""
        self._connected = False
        if self.response:
            self.response.close()
            self.response = None


class OpenClawClient:
    """
    Background client for OpenClaw gateway status using SSE.
    
    Runs in a separate thread and updates a shared state dictionary.
    Falls back to polling if SSE is unavailable.
    Uses a queue for async communication with the main snarling loop.
    """
    
    DEFAULT_GATEWAY_URL = "http://localhost:3000"
    DEFAULT_POLL_INTERVAL = 2.0  # seconds
    DEFAULT_TIMEOUT = 5.0  # seconds
    SSE_RETRY_DELAY = 5.0  # seconds before reconnecting SSE
    
    def __init__(
        self,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT
    ):
        self.gateway_url = gateway_url.rstrip('/')
        self.poll_interval = poll_interval
        self.timeout = timeout
        
        # Shared state - thread-safe dict
        self._state: Dict[str, Any] = {
            "connected": False,
            "snarling_state": snarlingState.UNKNOWN.value,
            "raw_state": "unknown",
            "last_update": 0,
            "error": None,
            "session_id": None,
            "agent_name": None,
            "using_sse": False,
        }
        self._state_lock = threading.RLock()
        
        # Message queue for async communication
        self.message_queue: Queue[Dict[str, Any]] = Queue()
        
        # Threading
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Health tracking
        self._consecutive_errors = 0
        self._max_consecutive_errors = 3
        
        # SSE client
        self._sse_client: Optional[SSEClient] = None
        self._use_sse = True  # Try SSE first, fallback to polling
    
    def _get_status_endpoint(self) -> str:
        """Build the status endpoint URL."""
        return f"{self.gateway_url}/api/status"
    
    def _get_sse_endpoint(self) -> str:
        """Build the SSE endpoint URL."""
        return f"{self.gateway_url}/api/status/stream"
    
    def _parse_state(self, raw_state: str) -> OpenClawState:
        """Parse raw state string to OpenClawState enum."""
        state_lower = raw_state.lower()
        
        for state in OpenClawState:
            if state.value == state_lower:
                return state
        
        # Handle variations
        if state_lower in ('busy', 'thinking', 'generating'):
            return OpenClawState.PROCESSING
        elif state_lower in ('talking', 'chatting', 'replying'):
            return OpenClawState.RESPONDING
        elif state_lower in ('offline', 'disconnected'):
            return OpenClawState.IDLE
        
        logger.debug(f"Unknown OpenClaw state: {raw_state}")
        return OpenClawState.UNKNOWN
    
    def _map_to_snarling_state(self, openclaw_state: OpenClawState) -> snarlingState:
        """Map OpenClaw state to snarling state."""
        return STATE_MAPPING.get(openclaw_state, snarlingState.UNKNOWN)
    
    def _update_state(self, status_data: Optional[Dict[str, Any]], error: Optional[str] = None, using_sse: bool = False):
        """
        Update shared state with new status data.
        
        Args:
            status_data: Parsed JSON response from gateway
            error: Error message if fetch failed
            using_sse: Whether this update came from SSE
        """
        with self._state_lock:
            if error:
                self._consecutive_errors += 1
                self._state["connected"] = False
                self._state["error"] = error
                self._state["snarling_state"] = snarlingState.ERROR.value
                self._state["using_sse"] = False
                
                # Only log errors occasionally to avoid spam
                if self._consecutive_errors <= self._max_consecutive_errors:
                    logger.warning(f"OpenClaw connection error ({self._consecutive_errors}/{self._max_consecutive_errors}): {error}")
                elif self._consecutive_errors == self._max_consecutive_errors + 1:
                    logger.info("OpenClaw client: suppressing further error messages until reconnected")
                    
            else:
                # Reset error counter on success
                was_disconnected = not self._state["connected"]
                was_not_using_sse = not self._state.get("using_sse", False)
                self._consecutive_errors = 0
                
                # Extract state from various possible response formats
                raw_state = status_data.get('status', status_data.get('state', 'unknown'))
                session_id = status_data.get('sessionId', status_data.get('session_id'))
                agent_name = status_data.get('agent', status_data.get('agentName'))
                
                print(f"[{datetime.now()}] OpenClaw client: received raw_state='{raw_state}'")
                
                # Map to snarling state
                openclaw_state = self._parse_state(raw_state)
                snarling_state = self._map_to_snarling_state(openclaw_state)
                
                print(f"[{datetime.now()}] OpenClaw client: mapped '{raw_state}' -> OpenClawState.{openclaw_state.name} -> snarlingState.{snarling_state.name}")
                
                # Update state
                self._state["connected"] = True
                self._state["raw_state"] = raw_state
                self._state["snarling_state"] = snarling_state.value
                self._state["last_update"] = time.time()
                self._state["error"] = None
                self._state["session_id"] = session_id
                self._state["agent_name"] = agent_name
                self._state["using_sse"] = using_sse
                
                if was_disconnected or (using_sse and was_not_using_sse):
                    source = "SSE" if using_sse else "polling"
                    logger.info(f"OpenClaw connected via {source}. State: {snarling_state.value}")
                
                # Put state change message in queue
                self.message_queue.put({
                    "type": "state_change",
                    "timestamp": time.time(),
                    "data": dict(self._state)
                })
    
    def _fetch_status_poll(self) -> Optional[Dict[str, Any]]:
        """
        Fetch status from OpenClaw gateway via polling.
        
        Returns:
            Parsed JSON response or None if failed
        """
        url = self._get_status_endpoint()
        
        try:
            response = requests.get(
                url,
                headers={'Accept': 'application/json'},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"HTTP error from OpenClaw gateway: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from OpenClaw gateway: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching OpenClaw status: {e}")
            return None
    
    def _poll_loop(self):
        """Polling loop running in background thread."""
        logger.info(f"OpenClaw polling started: {self.gateway_url}")
        
        while not self._stop_event.is_set():
            try:
                status_data = self._fetch_status_poll()
                
                if status_data is not None:
                    self._update_state(status_data, using_sse=False)
                else:
                    self._update_state(None, error="Failed to fetch status from OpenClaw gateway")
                    
            except Exception as e:
                logger.exception("Unexpected error in OpenClaw poll loop")
                self._update_state(None, error=str(e))
            
            # Wait for next poll interval or until stopped
            self._stop_event.wait(self.poll_interval)
        
        logger.info("OpenClaw polling stopped")
    
    def _sse_loop(self):
        """SSE loop running in background thread."""
        logger.info(f"OpenClaw SSE started: {self._get_sse_endpoint()}")
        
        while not self._stop_event.is_set():
            try:
                self._sse_client = SSEClient(self._get_sse_endpoint(), timeout=self.timeout)
                
                for event in self._sse_client.connect():
                    if self._stop_event.is_set():
                        break
                    
                    # Parse event data
                    data = event.get('data')
                    if isinstance(data, dict):
                        self._update_state(data, using_sse=True)
                        logger.debug(f"SSE event received: {data}")
                
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"SSE connection lost: {e}")
                    self._update_state(None, error=f"SSE connection lost: {e}", using_sse=False)
                    
                    # Wait before reconnecting
                    logger.info(f"Retrying SSE in {self.SSE_RETRY_DELAY} seconds...")
                    self._stop_event.wait(self.SSE_RETRY_DELAY)
            finally:
                if self._sse_client:
                    self._sse_client.close()
                    self._sse_client = None
        
        logger.info("OpenClaw SSE stopped")
    
    def _main_loop(self):
        """
        Main client loop that tries SSE first, then falls back to polling.
        """
        # Try SSE first
        if self._use_sse:
            logger.info("Attempting to connect via SSE...")
            try:
                # Test if SSE endpoint exists
                response = requests.get(
                    self._get_sse_endpoint(),
                    headers={'Accept': 'text/event-stream'},
                    timeout=5,
                    stream=True
                )
                
                if response.status_code == 200:
                    response.close()
                    logger.info("SSE endpoint available, using SSE streaming")
                    self._sse_loop()
                    return
                else:
                    response.close()
                    logger.info(f"SSE endpoint returned {response.status_code}, falling back to polling")
                    
            except Exception as e:
                logger.info(f"SSE not available: {e}")
        
        # Fall back to polling
        logger.info("Using polling fallback")
        self._poll_loop()
    
    def start(self) -> bool:
        """
        Start the background client thread.
        
        Returns:
            True if started successfully, False if already running
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("OpenClaw client already running")
            return False
        
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        
        # Give it a moment to start
        time.sleep(0.1)
        return True
    
    def stop(self, timeout: float = 5.0) -> bool:
        """
        Stop the background client thread.
        
        Args:
            timeout: Seconds to wait for thread to stop
            
        Returns:
            True if stopped successfully
        """
        if self._thread is None or not self._thread.is_alive():
            return True
        
        self._stop_event.set()
        
        # Close SSE connection if active
        if self._sse_client:
            self._sse_client.close()
        
        self._thread.join(timeout=timeout)
        
        if self._thread.is_alive():
            logger.warning("OpenClaw client thread did not stop in time")
            return False
        
        return True
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get a snapshot of the current shared state.
        
        Returns:
            Copy of current state dictionary
        """
        with self._state_lock:
            return dict(self._state)
    
    def get_snarling_state(self) -> snarlingState:
        """
        Get the current snarling state.
        
        Returns:
            Current snarlingState enum value
        """
        with self._state_lock:
            state_value = self._state.get("snarling_state", snarlingState.UNKNOWN.value)
        
        try:
            return snarlingState(state_value)
        except ValueError:
            return snarlingState.UNKNOWN
    
    def is_connected(self) -> bool:
        """Check if OpenClaw gateway is connected."""
        with self._state_lock:
            return self._state["connected"]
    
    def is_using_sse(self) -> bool:
        """Check if currently using SSE (vs polling)."""
        with self._state_lock:
            return self._state.get("using_sse", False)
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on the OpenClaw connection.
        
        Returns:
            Health check result dictionary
        """
        with self._state_lock:
            last_update = self._state["last_update"]
            connected = self._state["connected"]
            using_sse = self._state.get("using_sse", False)
        
        time_since_update = time.time() - last_update if last_update > 0 else float('inf')
        
        status = "healthy" if connected else "unhealthy"
        if connected and time_since_update > self.poll_interval * 3:
            status = "stale"
        
        return {
            "status": status,
            "connected": connected,
            "using_sse": using_sse,
            "time_since_update_seconds": time_since_update,
            "poll_interval": self.poll_interval,
            "gateway_url": self.gateway_url,
            "sse_endpoint": self._get_sse_endpoint(),
            "last_error": self._state.get("error"),
        }
    
    def get_message(self, block: bool = False, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Get a message from the async queue.
        
        Args:
            block: Whether to block until a message is available
            timeout: Maximum time to wait if blocking
            
        Returns:
            Message dictionary or None if no message available
        """
        try:
            return self.message_queue.get(block=block, timeout=timeout)
        except Exception:
            return None
    
    def has_messages(self) -> bool:
        """Check if there are pending messages in the queue."""
        return not self.message_queue.empty()


class OpenClawIntegration:
    """
    High-level OpenClaw integration for snarling.
    
    Provides a simple interface for the main snarling loop.
    """
    
    def __init__(
        self,
        gateway_url: str = OpenClawClient.DEFAULT_GATEWAY_URL,
        poll_interval: float = OpenClawClient.DEFAULT_POLL_INTERVAL
    ):
        self.client = OpenClawClient(
            gateway_url=gateway_url,
            poll_interval=poll_interval
        )
        self._running = False
    
    def start(self) -> bool:
        """Start the OpenClaw integration."""
        if self._running:
            return True
        
        success = self.client.start()
        if success:
            self._running = True
            logger.info("OpenClaw integration started")
        return success
    
    def stop(self) -> bool:
        """Stop the OpenClaw integration."""
        if not self._running:
            return True
        
        success = self.client.stop()
        self._running = False
        return success
    
    def get_state(self) -> snarlingState:
        """Get current snarling state from OpenClaw."""
        return self.client.get_snarling_state()
    
    def is_healthy(self) -> bool:
        """Check if OpenClaw connection is healthy."""
        health = self.client.health_check()
        return health["status"] == "healthy"
    
    def is_using_sse(self) -> bool:
        """Check if currently using SSE."""
        return self.client.is_using_sse()
    
    def check_messages(self) -> list:
        """Check for and return all pending state change messages."""
        messages = []
        while self.client.has_messages():
            msg = self.client.get_message(block=False)
            if msg:
                messages.append(msg)
        return messages
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# Convenience functions for direct usage

def create_client(gateway_url: str = "http://localhost:3000") -> OpenClawClient:
    """Create and return a new OpenClawClient instance."""
    return OpenClawClient(gateway_url=gateway_url)


def create_integration(
    gateway_url: str = "http://localhost:3000",
    poll_interval: float = 2.0
) -> OpenClawIntegration:
    """Create and return a new OpenClawIntegration instance."""
    return OpenClawIntegration(gateway_url=gateway_url, poll_interval=poll_interval)


# Example usage / testing
if __name__ == "__main__":
    import sys
    
    print("OpenClaw snarling Integration Test")
    print("=" * 50)
    
    # Create and start integration
    integration = create_integration()
    
    try:
        integration.start()
        
        # Run for 30 seconds, printing state changes
        print("\nMonitoring OpenClaw for 30 seconds...")
        print("Press Ctrl+C to stop early\n")
        
        start_time = time.time()
        last_state = None
        last_source = None
        
        while time.time() - start_time < 30:
            # Check health
            health = integration.client.health_check()
            
            # Get current state
            current_state = integration.get_state()
            current_source = "SSE" if integration.is_using_sse() else "poll"
            
            # Print state changes
            if current_state != last_state or current_source != last_source:
                source_icon = "📡" if current_source == "SSE" else "🔄"
                print(f"[{time.strftime('%H:%M:%S')}] {source_icon} State: {current_state.value.upper()}")
                if not health["connected"]:
                    print(f"  -> Not connected to OpenClaw gateway")
                last_state = current_state
                last_source = current_source
            
            # Check for messages
            messages = integration.check_messages()
            for msg in messages:
                data = msg['data']
                print(f"  [Message] {data.get('agent_name', 'unknown')} @ {data.get('session_id', 'unknown')[:8]}...")
            
            time.sleep(0.5)
        
        print("\nTest complete!")
        print(f"Final health: {integration.client.health_check()}")
        
    except KeyboardInterrupt:
        print("\n\nStopped by user")
    finally:
        integration.stop()
        print("Integration stopped")
