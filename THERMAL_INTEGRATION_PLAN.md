# Thermal Sensor Integration Plan for snarling.py

*Created: 2026-04-26*

## Overview

This plan details how to integrate `thermal.py` (MLX90640 thermal sensor) into `snarling.py`, giving the creature awareness of human presence and proximity. The integration follows the architecture described in the [brainstorm doc](../general-files/thermal-sensor-brainstorm.md): thermal.py runs as a thread inside the snarling process, calling snarling's rendering functions directly for sub-100ms physical reactions. No HTTP loopback for the fast path.

**Key principle:** Agent state wins for face, proximity wins for backlight. Except when sleeping — then proximity drives both.

---

## 1. Startup Integration

### Location: `snarlingCreature.__init__()` (around line 100)

Add after the LED/animation init block (`self.led_timer = 0`):

```python
# Thermal sensor integration
self.thermal = None
self._thermal_available = False
try:
    from thermal import ThermalSensor
    self.thermal = ThermalSensor(
        on_presence_change=self._on_thermal_presence_change,
        on_proximity_change=self._on_thermal_proximity_change,
    )
    self._thermal_available = True
    print("[snarling] Thermal sensor initialized (MLX90640)")
except ImportError:
    print("[snarling] thermal.py not found — presence detection disabled")
except Exception as e:
    print(f"[snarling] Thermal sensor init failed: {e} — presence detection disabled")
    self.thermal = None
    self._thermal_available = False
```

### Location: `snarlingCreature.cleanup()`

Add thermal cleanup:

```python
if self.thermal is not None:
    try:
        self.thermal.stop()
    except Exception:
        pass
```

### Key design decisions:
- Import is attempted inside `__init__`, not at module top — this matches the existing pattern for DisplayHATMini (snarling starts normally if hardware isn't present)
- The `ThermalSensor` class doesn't exist yet — it will be created in `thermal.py` with a `start()` call happening inside `__init__` and a `stop()` for cleanup
- All thermal-related attributes default to "no thermal" values so the render loop works unchanged when thermal is unavailable

---

## 2. Callbacks from ThermalSensor to Snarling

### New attributes in `__init__`:

```python
# Environmental state (merges thermal + external data)
self._environmental_state = {
    "present": False,
    "proximity": 0.0,
    "proximity_zone": "absent",  # "absent" | "approaching" | "present"
    "source": "none",             # "thermal" | "external" | "none"
    "last_change": None,          # ISO 8601 timestamp
    "ambient_temp": None,         # °C from thermal sensor
}
self._environmental_lock = threading.Lock()  # Thread safety for environmental state

# Proximity-driven brightness transitions
self._brightness_target = 0.2        # Target backlight brightness (0.0–1.0)
self._brightness_current = 0.2        # Current backlight brightness (interpolated)
self._brightness_ramp_start = 0      # time.time() when current ramp started
self._brightness_ramp_duration = 0.7  # seconds for ease-out cubic ramp

# Proximity-driven face state (only used when agent state is sleeping)
self._proximity_face_pending = None   # Face to show after delay
self._proximity_face_time = 0         # When to show the pending face
self._proximity_face_current = "(≖◡◡≖)"  # Current proximity-driven face
```

### Callback: `on_presence_change(absent, present)`

```python
def _on_thermal_presence_change(self, absent, present):
    """Called by ThermalSensor when someone appears or disappears.
    Runs in the thermal sensor's thread — must be thread-safe."""
    now = time.time()
    with self._environmental_lock:
        self._environmental_state["present"] = present
        self._environmental_state["last_change"] = now
        if present:
            self._environmental_state["proximity_zone"] = "approaching"
            self._environmental_state["proximity"] = max(self._environmental_state["proximity"], 0.3)
        else:
            self._environmental_state["proximity_zone"] = "absent"
            self._environmental_state["proximity"] = 0.0
        self._environmental_state["source"] = "thermal"

    if present:
        # Person appeared — schedule face/brightness transition
        # 150ms delay before reacting (feels like perception, not a sensor)
        self._proximity_face_pending = "(⊙◡⊙)"  # Awareness: eyes widening
        self._proximity_face_time = now + 0.15
        # Start brightness ramp toward "approaching" level
        self._brightness_target = 0.5
        self._brightness_ramp_start = now + 0.15  # Delay matches face
        self._brightness_ramp_duration = 0.7      # 700ms ease-out cubic
    else:
        # Person disappeared — dim down
        self._proximity_face_pending = "(≖◡◡≖)"  # Dozing
        # No delay on disappearance — immediate start
        self._proximity_face_time = now
        self._brightness_target = 0.2
        self._brightness_ramp_start = now
        self._brightness_ramp_duration = 0.9  # Slower fade-out

    print(f"[snarling] Presence change: absent={absent}, present={present}")
```

### Callback: `on_proximity_change(old_zone, new_zone, proximity)`

```python
def _on_thermal_proximity_change(self, old_zone, new_zone, proximity):
    """Called by ThermalSensor when proximity zone changes.
    Runs in the thermal sensor's thread — must be thread-safe."""
    now = time.time()
    with self._environmental_lock:
        self._environmental_state["proximity"] = proximity
        self._environmental_state["proximity_zone"] = new_zone
        self._environmental_state["last_change"] = now
        self._environmental_state["source"] = "thermal"

    # Brightness targets based on proximity zone
    if new_zone == "present":
        # Close range — full brightness (with slight overshoot for "lock-on")
        self._brightness_target = 1.05  # Overshoot, settles to 1.0
        self._proximity_face_pending = "(◠‿◠)"  # I see you
        self._proximity_face_time = now + 0.20  # 200ms after detection
        self._brightness_ramp_start = now
        self._brightness_ramp_duration = 0.7
    elif new_zone == "approaching":
        # Mid range — partial brightness
        self._brightness_target = 0.3 + proximity * 0.7  # Scale with proximity
        self._proximity_face_pending = "(⊙◡⊙)"  # Awareness
        self._proximity_face_time = now + 0.15
        self._brightness_ramp_start = now
        self._brightness_ramp_duration = 0.7
    else:
        # Absent — dim
        self._brightness_target = 0.2
        self._proximity_face_pending = "(≖◡◡≖)"  # Dozing
        self._proximity_face_time = now
        self._brightness_ramp_start = now
        self._brightness_ramp_duration = 0.9

    print(f"[snarling] Proximity change: {old_zone} -> {new_zone} ({proximity:.2f})")
```

### Timing model (from brainstorm):
- **Detection → reaction delay:** ~150ms (feels like perception, not a sensor trigger)
- **Brightness ramp:** ease-out cubic over 600–900ms (fast start, slow settle)
- **Face transition:** staged — awareness `(⊙◡⊙)` at 200ms, engagement `(◠‿◠)` only when "close"
- **Overshoot:** brightness briefly targets 1.05, then settles to 1.0 (feels like "it adjusted to you")

---

## 3. Render Loop Changes

### 3a. Backlight Brightness

Currently, snarling doesn't directly control backlight brightness — the display is always at full brightness with the LED being the only brightness control. We'll use the LED color/brightness as the proxy for "ambient brightness" since the DisplayHATMini doesn't have a backlight PWM.

**However**, the brainstorm mentions backlight specifically. The actual effect should be:
1. The **LED color and brightness** reflects proximity (warm when present, cool when absent)
2. The **screen background darkness** adjusts based on proximity (darker when absent, slightly brighter when present)

#### Changes to `update_led()`:

Add proximity-driven LED behavior **after** existing state-based LED logic. The proximity effect composites on top:

```python
def update_led(self):
    """Update LED based on state, breathing animation, and proximity"""
    if self.screen_asleep:
        self.display.set_led(0, 0, 0)
        return

    # Get current environmental state (thread-safe)
    with self._environmental_lock:
        env_present = self._environmental_state["present"]
        env_proximity = self._environmental_state["proximity"]

    # Calculate proximity brightness using ease-out cubic ramp
    now = time.time()
    elapsed = now - self._brightness_ramp_start
    if elapsed < self._brightness_ramp_duration:
        # Ease-out cubic: fast start, slow settle
        t = elapsed / self._brightness_ramp_duration
        t = 1 - (1 - t) ** 3  # ease-out cubic
        self._brightness_current = self._brightness_current + (self._brightness_target - self._brightness_current) * t
    else:
        self._brightness_current = self._brightness_target
        # Clamp overshoot after settling
        self._brightness_current = min(self._brightness_current, 1.0)

    proximity_brightness = max(0.15, self._brightness_current)

    if self.led_timer > 0 or self.state in (STATE_SLEEPING, STATE_NOTIFYING, STATE_AWAITING_APPROVAL):
        # State-based LED (existing logic)
        # ... (existing code) ...
        pass
    elif env_present or env_proximity > 0.1:
        # Warm slow pulse proportional to proximity
        pulse = 0.3 + 0.4 * math.sin(self.breath_phase * 0.8) * env_proximity
        warmth = env_proximity  # 0.0 = cool, 1.0 = warm
        self.display.set_led(
            pulse * warmth * 0.8,          # Red component (warmth)
            pulse * 0.3,                     # Green (subtle)
            pulse * (1 - warmth) * 0.5      # Blue (cool when distant)
        )
    else:
        # Nobody present — cool slow breathing (existing sleeping behavior)
        brightness = 0.3 + 0.2 * math.sin(self.breath_phase)
        brightness *= 0.7
        self.display.set_led(0, brightness * 0.25, brightness * 0.5)
```

### 3b. Face Selection

Modify `update_face()` to consider proximity when the agent is sleeping:

```python
def update_face(self, dt):
    """Update face animation and expression"""
    self.face_timer += dt

    # Check for pending proximity-driven face transition (with delay)
    now = time.time()
    if self._proximity_face_pending and now >= self._proximity_face_time:
        self._proximity_face_current = self._proximity_face_pending
        self._proximity_face_pending = None

    if self.face_timer > 2.0:
        # Priority: agent state wins for face EXCEPT when sleeping
        if self.state == STATE_SLEEPING:
            # Proximity drives face when sleeping
            with self._environmental_lock:
                zone = self._environmental_state["proximity_zone"]
            
            if zone == "present":
                faces = ["(◠‿◠)"]  # Warm recognition — "I see you"
            elif zone == "approaching":
                faces = ["(⊙◡⊙)"]  # Eyes widening — "oh, you're here"
            else:
                faces = FaceExpressions.SLEEP  # Dozing: (≖◡◡≖), (⇀‿‿↼)
        elif self.state == STATE_NOTIFYING and self._notify_active:
            # ... (existing notification face logic) ...
            pass
        else:
            # Agent state wins for all other states
            faces = FaceExpressions.get_faces_for_state(self.state, getattr(self, '_notify_priority', None))

        if faces:
            self.face_index = (self.face_index + 1) % len(faces)
            self.current_face = faces[self.face_index]
        self.face_timer = 0

    # ... (existing animation offset logic) ...
```

**Note on staged transitions:** When a proximity face change is pending (the `_proximity_face_pending` / `_proximity_face_time` mechanism), the face doesn't change instantly. It waits for the perceptual delay (150–200ms), which creates the "it noticed you" feeling. When the delay expires, the face transitions in the next `update_face` cycle.

### 3c. Background Color

When nobody is present and agent is sleeping, darken the background slightly:

```python
def draw_background(self):
    """Fill background — slightly brighter when someone is present"""
    with self._environmental_lock:
        env_proximity = self._environmental_state["proximity"]
    
    # Interpolate between deep dark (absent) and slightly less dark (present)
    r = int(26 + env_proximity * 10)  # 26 → 36
    g = int(26 + env_proximity * 10)   # 26 → 36
    b = int(46 + env_proximity * 8)    # 46 → 54
    self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(r, g, b))
```

This is subtle — just enough to feel like the screen "wakes up" slightly when you approach.

---

## 4. New Flask Endpoints

### GET /presence

Returns current presence info. Added alongside existing `/health`, `/state`, `/counts` endpoints:

```python
@approval_app.route('/presence', methods=['GET'])
def get_presence():
    """Return current presence data from thermal sensor (or external source)"""
    global creature_instance
    if not creature_instance:
        return jsonify({"thermal_available": False, "present": False, "proximity": 0.0}), 503
    
    with creature_instance._environmental_lock:
        state = dict(creature_instance._environmental_state)
    
    # Convert last_change from epoch to ISO 8601
    last_change = None
    if state.get("last_change"):
        from datetime import datetime, timezone
        last_change = datetime.fromtimestamp(state["last_change"], tz=timezone.utc).isoformat()
    
    return jsonify({
        "present": state["present"],
        "proximity": state["proximity"],
        "proximity_zone": state["proximity_zone"],
        "ambient_temp": state.get("ambient_temp"),
        "last_change": last_change,
        "thermal_available": creature_instance._thermal_available,
    })
```

### POST /environment

External input for presence data (for plugin or other sensors):

```python
@approval_app.route('/environment', methods=['POST'])
def set_environment():
    """Receive environmental data from external sources (plugin, PIR sensor, etc.)
    Thermal sensor data takes precedence when available."""
    global creature_instance
    if not creature_instance:
        return jsonify({"error": "snarling not initialized"}), 503
    
    data = request.json
    if not data:
        return jsonify({"error": "No JSON data"}), 400
    
    # Validate required fields
    present = data.get("present", False)
    proximity = data.get("proximity", 0.0)
    state = data.get("state", "aware")  # "aware" | "sleeping" | etc.
    
    # Thermal data takes precedence — external data only fills in when thermal is unavailable
    if creature_instance._thermal_available:
        return jsonify({
            "status": "ignored", 
            "reason": "thermal_sensor_active",
            "message": "Thermal sensor data takes precedence. External data ignored."
        })
    
    # No thermal sensor — use external data
    now = time.time()
    
    # Map external state to proximity zone
    if present and proximity >= 0.6:
        zone = "present"
    elif present:
        zone = "approaching"
    else:
        zone = "absent"
    
    with creature_instance._environmental_lock:
        creature_instance._environmental_state["present"] = present
        creature_instance._environmental_state["proximity"] = proximity
        creature_instance._environmental_state["proximity_zone"] = zone
        creature_instance._environmental_state["source"] = "external"
        creature_instance._environmental_state["last_change"] = now
    
    # Trigger face/brightness transitions (same callbacks, but direct)
    if present:
        creature_instance._proximity_face_pending = "(⊙◡⊙)"
        creature_instance._proximity_face_time = now + 0.15
        creature_instance._brightness_target = 0.3 + proximity * 0.7
        creature_instance._brightness_ramp_start = now
        creature_instance._brightness_ramp_duration = 0.7
    else:
        creature_instance._proximity_face_pending = "(≖◡◡≖)"
        creature_instance._proximity_face_time = now
        creature_instance._brightness_target = 0.2
        creature_instance._brightness_ramp_start = now
        creature_instance._brightness_ramp_duration = 0.9
    
    return jsonify({
        "status": "ok",
        "present": present,
        "proximity": proximity,
        "proximity_zone": zone,
    })
```

---

## 5. Notification Callback Enhancement

### Modify `forward_notification_feedback()`

Add `present` field to the feedback payload sent to the gateway:

```python
def forward_notification_feedback(self, revealed, time_to_reveal_sec, dismissed, timed_out=False, time_in_queue_sec=0):
    # ... (existing code) ...
    
    # Add presence data from environmental state
    with self._environmental_lock:
        present_at_feedback = self._environmental_state["present"]
        proximity_at_feedback = self._environmental_state["proximity"]
    
    response_data = {
        "notification_id": notify_id,
        "revealed": revealed,
        "time_to_reveal_sec": time_to_reveal_sec + time_in_queue_sec,
        "dismissed": dismissed,
        "timed_out": timed_out,
        "secret": secret,
        "sessionKey": session_key,
        "present": present_at_feedback,         # NEW: Was someone there?
        "proximity": proximity_at_feedback,     # NEW: How close were they?
    }
    # ... (rest of existing code) ...
```

This is critical for the attunement feedback loop: a timed-out notification with `present: false` is meaningless (no one was there to see it), while `timed_out + present: true` is real negative signal.

---

## 6. Environmental State

### Data Structure

Already defined in Section 2. The `self._environmental_state` dictionary is the single source of truth:

```python
self._environmental_state = {
    "present": False,          # Is someone detected?
    "proximity": 0.0,          # 0.0–1.0, how close
    "proximity_zone": "absent", # "absent" | "approaching" | "present"
    "source": "none",          # "thermal" | "external" | "none"
    "last_change": None,        # epoch timestamp of last change
    "ambient_temp": None,       # °C from thermal sensor (optional)
}
```

### Merge Priority

| Source | When Used | Priority |
|--------|-----------|----------|
| Thermal sensor | Always, when available | **Highest** |
| External `/environment` | Only when thermal unavailable | Fallback |
| None | Default state | Baseline |

The `/environment` endpoint checks `self._thermal_available` before accepting external data. If thermal is running, external data is ignored with a clear response.

### What reads environmental_state:

1. **Render loop** (brightness, face, background) — reads every frame via `_environmental_lock`
2. **`/presence` endpoint** — reads on HTTP request
3. **`forward_notification_feedback()`** — reads when feedback is sent
4. **Thermal callbacks** — writes from thermal thread
5. **`/environment` endpoint** — writes from Flask thread (only when thermal unavailable)

All reads/writes go through `self._environmental_lock` (a `threading.Lock`).

---

## 7. Thread Safety

### Threads in play:

| Thread | Role | Reads | Writes |
|--------|------|-------|--------|
| **Main** | Render loop (30fps) | `_environmental_state`, `_brightness_*`, `_proximity_face_*` | Renders frame |
| **Thermal** | `ThermalSensor` reading MLX90640 | — | `_environmental_state`, `_brightness_*`, `_proximity_face_*` |
| **Flask** | HTTP server handling `/state`, `/presence`, `/environment` | `_environmental_state` | `_environmental_state` (via `/environment`) |

### Strategy:

1. **`_environmental_lock`** (`threading.Lock`): Protects `_environmental_state` dictionary. Both the thermal callback and Flask `/environment` endpoint write to it; the render loop and Flask `/presence` endpoint read from it.

2. **Brightness ramp attributes** (`_brightness_target`, `_brightness_current`, `_brightness_ramp_start`, `_brightness_ramp_duration`): These are simple float values written by callbacks and read by the render loop. Since Python floats are atomic for read/write on CPython, and only one callback writes at a time, no additional lock is needed. The render loop reads them each frame and interpolates.

3. **Proximity face attributes** (`_proximity_face_pending`, `_proximity_face_time`, `_proximity_face_current`): Same pattern — written by callbacks, read by render loop. No lock needed for these simple assignments.

4. **`ThermalSensor`** should use its own `threading.Lock` internally for its `present` and `proximity` attributes, so that the `/presence` endpoint can safely read them. Alternatively, the endpoint can just read from `snarling._environmental_state` (which is already protected by `_environmental_lock`).

### ThermalSensor internal thread safety (for thermal.py):

```python
class ThermalSensor:
    def __init__(self, on_presence_change, on_proximity_change):
        self._lock = threading.Lock()
        self._present = False
        self._proximity = 0.0
        self._proximity_zone = "absent"
        self._ambient_temp = None
        # ... (callback refs, thread, etc.)
    
    @property
    def present(self):
        with self._lock:
            return self._present
    
    @property
    def proximity(self):
        with self._lock:
            return self._proximity
    
    # Internal updates also use the lock
    def _update_presence(self, present):
        with self._lock:
            self._present = present
```

### Graceful error handling in render loop:

If reading `_environmental_state` raises an exception (shouldn't happen with a Lock, but defensive), fall back to defaults:

```python
try:
    with self._environmental_lock:
        env_present = self._environmental_state["present"]
        env_proximity = self._environmental_state["proximity"]
except Exception:
    env_present = False
    env_proximity = 0.0
```

---

## 8. Graceful Degradation

### Scenario: thermal.py import fails

- `self.thermal = None`, `self._thermal_available = False`
- `self._environmental_state` starts with `source: "none"`, `present: False`, `proximity: 0.0`
- Render loop sees no presence → behaves exactly like current snarling (no proximity effects)
- `/presence` returns `{thermal_available: false, present: false, proximity: 0.0}`
- `/environment` endpoint accepts external data as fallback
- No crashes, no errors in normal operation — just no thermal awareness

### Scenario: thermal sensor crashes at runtime

- `ThermalSensor` should catch its own exceptions and not propagate them to snarling
- If the thermal thread dies, `self.thermal` is still set but its internal running flag is `False`
- Add a periodic health check in the render loop (every 5 seconds):

```python
# In update() method
self._thermal_health_counter += 1
if self._thermal_health_counter >= 150:  # ~5 seconds at 30fps
    self._thermal_health_counter = 0
    if self.thermal and not self.thermal.is_running:
        print("[snarling] Thermal sensor thread died — marking as unavailable")
        self._thermal_available = False
        with self._environmental_lock:
            self._environmental_state["present"] = False
            self._environmental_state["proximity"] = 0.0
            self._environmental_state["proximity_zone"] = "absent"
        self._brightness_target = 0.2
```

### Scenario: /environment receives data while thermal is active

- Endpoint returns `{status: "ignored", reason: "thermal_sensor_active"}`
- External data is not applied to `_environmental_state`
- Clear message so the plugin knows to not waste bandwidth

### Scenario: /environment receives data while thermal is unavailable

- External data fills in for the missing thermal sensor
- Face/brightness transitions work the same way as thermal callbacks
- `source` field in `_environmental_state` is set to `"external"` so `/presence` reports the data source

---

## 9. Face Expression Reference

| Condition | Face | Notes |
|-----------|------|-------|
| Sleeping + nobody present | `(≖◡◡≖)` / `(⇀‿‿↼)` | Dozing, existing SLEEP faces |
| Sleeping + approaching | `(⊙◡⊙)` | Eyes widening — "oh, you're here" |
| Sleeping + present (close) | `(◠‿◠)` | Warm recognition — "I see you" |
| Processing + nobody present | `(◕‿‿◕)` / existing | Agent face wins, but backlight dims |
| Processing + present | `(◕‿‿◕)` / existing | Agent face wins, backlight bright |
| Communicating + present | `(ᵔ◡◡ᵔ)` / existing | Agent face wins, backlight bright |
| Notifying (any proximity) | Priority faces | Notification face always wins |
| Awaiting approval (any) | `(⚆_⚆)` / existing | Approval face always wins |

**New faces to add to `FaceExpressions`:**

```python
# Proximity-aware faces (used when sleeping + someone detected)
PROXIMITY_APPROACHING = ['(⊙◡⊙)']  # Eyes widening — awareness
PROXIMITY_PRESENT = ['(◠‿◠)']        # Warm recognition — engagement
PROXIMITY_ABSENT = ['(≖◡◡≖)']         # Relaxed dozing (already in SLEEP)
```

These are intentionally single-expression lists (not animated). The face changes are the animation — staging from `(≖◡◡≖)` → `(⊙◡⊙)` → `(◠‿◠)` as someone approaches.

---

## 10. Brightness Ramp Implementation

### Ease-out cubic function:

```python
@staticmethod
def _ease_out_cubic(t):
    """Ease-out cubic: fast start, slow settle. t in [0, 1]."""
    return 1 - (1 - t) ** 3
```

### Brightness interpolation in render loop:

The render loop (`update()` method) interpolates `_brightness_current` toward `_brightness_target` each frame using the ease-out cubic curve:

```python
# In update() method, after breath_phase update
now = time.time()
elapsed = now - self._brightness_ramp_start
if elapsed < self._brightness_ramp_duration and self._brightness_ramp_duration > 0:
    t = elapsed / self._brightness_ramp_duration
    eased_t = self._ease_out_cubic(t)
    # Interpolate from wherever we were when the ramp started
    # We need to track the start value for proper interpolation
    ramp_progress = eased_t
else:
    ramp_progress = 1.0  # Ramp complete

# Smoothly move current brightness toward target
# Using simple exponential smoothing per frame as alternative:
target = self._brightness_target
self._brightness_current += (target - self._brightness_current) * 0.1  # ~3 frames to 95%
```

**Implementation note:** The simplest approach is exponential smoothing in the render loop rather than tracking ramp start values. This avoids complexity with overlapping ramps (e.g., someone approaches then leaves mid-ramp). The time-constant naturally handles interruptions:

```python
# Simple, robust approach — per-frame exponential smoothing
# At 30fps, 0.1 smoothing factor ≈ 0.7s to 95% of target
# This produces a natural ease-out curve without tracking ramp state
frame_dt = 1.0 / 30.0
alpha = 1 - math.exp(-frame_dt / 0.23)  # ~0.7s time constant
self._brightness_current += (self._brightness_target - self._brightness_current) * alpha
```

This is simpler and more robust than the explicit ramp tracking. The exponential smoothing naturally produces ease-out behavior and handles interrupted transitions gracefully.

---

## 11. Integration with Existing Notification/Approval System

### State priority for face:

```
1. STATE_NOTIFYING (notification faces always win)
2. STATE_AWAITING_APPROVAL (approval faces always win)
3. STATE_SLEEPING + proximity (proximity drives face)
4. STATE_PROCESSING / STATE_COMMUNICATING / STATE_ERROR (agent state drives face)
```

### Proximity always affects backlight, regardless of state:

Even when the agent is processing or communicating, if someone walks away, the backlight should dim slightly. If someone approaches, it brightens. This makes the display feel alive even during active agent use.

### Notification callbacks include presence:

See Section 5. This is a non-breaking change — just adding two new fields to the existing JSON payload.

---

## 12. File Structure After Integration

```
snarling/
├── snarling.py          # Modified — adds thermal init, callbacks, render changes, Flask endpoints
├── thermal.py            # NEW — ThermalSensor class, MLX90640 driver, runs as thread
└── THERMAL_INTEGRATION_PLAN.md  # This file
```

### thermal.py responsibilities:

- Import and initialize MLX90640 via `adafruit_circuitpython_mlx90640`
- Run sensor reading loop in its own thread (~2Hz)
- Perform blob detection and noise filtering
- Compute: `present` (bool), `proximity` (0.0–1.0), `proximity_zone` (absent/approaching/present), `ambient_temp` (°C)
- Call `on_presence_change(absent, present)` when presence transitions
- Call `on_proximity_change(old_zone, new_zone, proximity)` when zone transitions
- Provide `.present`, `.proximity`, `.proximity_zone` properties for direct reads
- Gracefully handle: missing camera, I2C errors, import failures
- Use `threading.Lock` internally for thread-safe property access
- Have `stop()` method for clean shutdown

### snarling.py changes summary:

| Section | Change | Lines Affected |
|---------|--------|----------------|
| `__init__` | Add thermal init, environmental state, brightness/face transition attrs | ~20 lines added |
| New methods | `_on_thermal_presence_change`, `_on_thermal_proximity_change`, `_ease_out_cubic` | ~60 lines |
| `update()` | Add brightness interpolation, proximity face transition, thermal health check | ~20 lines |
| `update_face()` | Add proximity face logic when sleeping | ~15 lines |
| `update_led()` | Add proximity-driven LED behavior | ~20 lines |
| `draw_background()` | Add proximity-driven background darkness | ~5 lines |
| `forward_notification_feedback()` | Add `present` and `proximity` to payload | ~5 lines |
| `cleanup()` | Add thermal stop | ~3 lines |
| Flask routes | Add `/presence` GET and `/environment` POST | ~70 lines |
| **Total** | | **~220 lines added** |

---

## 13. Testing Plan

### Phase 1: No thermal hardware
- Start snarling without thermal.py present → works normally, logs "thermal.py not found"
- Start snarling with thermal.py that raises ImportError → works normally, logs "import failed"
- POST `/environment` with presence data → display reacts (dim/brighten, face changes)
- GET `/presence` → returns external data correctly
- Verify all existing functionality unchanged (notifications, approvals, state changes)

### Phase 2: With thermal hardware (MLX90640)
- Start snarling with thermal.py and MLX90640 → logs "Thermal sensor initialized"
- Walk toward display → face transitions `(≖◡◡≖)` → `(⊙◡⊙)` → `(◠‿◠)`, brightness ramps up
- Walk away → face returns to `(≖◡◡≖)`, brightness dims
- Verify 150ms detection delay feels natural
- Verify brightness ease-out cubic feels like "waking up"
- Test with agent in different states (processing, communicating) → proximity affects backlight but not face

### Phase 3: Edge cases
- Pull MLX90640 I2C connection while running → snarling continues, logs error, thermal marked unavailable
- Send `/environment` while thermal is active → returns "ignored" response
- Rapid approach/leave cycles → no flickering, smooth transitions
- Notification arrives while sleeping + present → notification face wins, backlight stays bright
- Approval arrives while sleeping + present → approval face wins, backlight stays bright

---

## 14. Open Questions for Implementation

1. **Brightness overshoot:** The brainstorm suggests briefly targeting 1.05 then settling to 1.0. This needs careful implementation to avoid visual artifacts. Consider using a two-phase ramp: 0→1.05 over 300ms, then 1.05→1.0 over 200ms.

2. **Walking away expression:** The brainstorm asks if there should be a brief `(◡‿◡)` → `(≖◡◡≖)` transition when someone leaves. This would need a "leaving" state that persists for ~500ms before fading to dozing. Add this to `on_presence_change(absent=True)`.

3. **Night mode:** Should the display go even dimmer (below 0.15) when nobody has been present for >30 minutes? This could be a future enhancement based on the `last_change` timestamp.

4. **Multiple people:** The MLX90640 can see multiple heat sources. Should proximity track the nearest person, or average? Start with nearest person (simplest, most intuitive).

5. **thermal.py location:** Should it be in the same directory as snarling.py (for direct import) or in a shared location? Plan assumes same directory. If thermal.py is elsewhere, the import path needs adjustment.

6. **Ambient temperature:** The MLX90640 provides ambient temperature. Should this be exposed via `/presence`? Yes — it's useful for the plugin/agent to know room temperature. But it's not used for any display decisions.