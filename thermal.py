"""
ThermalSensor — MLX90640 presence/proximity detection for snarling.

Runs as a daemon thread inside the snarling process. Provides thread-safe
presence state that snarling's render loop can query each frame for instant
physical reactions (the "fast path").

If the MLX90640 library or hardware isn't available, snarling starts normally
without thermal sensing — just log a single info message and continue.
"""

import logging
import threading
import time

# Configure logging so thermal.py messages are visible
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)

logger = logging.getLogger("snarling.thermal")

# ── Detection tuning ──────────────────────────────────────────────────
PERSON_DELTA_NORMAL = 3.0  # °C above ambient at normal room temp (<25°C)
PERSON_DELTA_WARM = 2.0    # °C above ambient when kitchen is warm (25-30°C)
PERSON_DELTA_HOT = 1.5    # °C above ambient when kitchen is hot (>30°C)
MIN_PERSON_PIXELS = 15    # minimum blob size to qualify as a person
MIN_BLOB_ASPECT = 0.25    # minimum width/height ratio (rejects tall narrow edge artifacts)
EDGE_MARGIN = 2           # ignore outermost N rows/columns (MLX90640 edge artifacts)
DEBOUNCE_FRAMES = 2       # Fast debounce for display (face/LED) — ~0.5s at 4Hz
PRESENCE_DEBOUNCE_FRAMES = 15  # ~3.75s at 4Hz — presence callbacks, logging, cascade
READ_INTERVAL = 0.25       # seconds between frames (~4 Hz)
ERROR_BACKOFF = 5.0       # seconds to wait after a read error


def person_delta(ambient):
    """Adaptive threshold: lower delta when ambient is high to reduce false negatives.

    When the kitchen is warm (stove on), the temperature difference between
    a person and the background shrinks. Lowering the threshold prevents
    false negatives where you disappear from detection.
    Tradeoff: more false positives from stove heat (acceptable).
    """
    if ambient < 25:
        return PERSON_DELTA_NORMAL
    elif ambient < 30:
        return PERSON_DELTA_WARM
    else:
        return PERSON_DELTA_HOT

# Proximity zone thresholds
ZONE_ABSENT = "absent"         # proximity == 0.0
ZONE_APPROACHING = "approaching"  # 0.3 <= proximity < 0.65
ZONE_PRESENT = "present"       # proximity >= 0.65

# ── Pure-Python connected-component labelling (flood fill) ───────────

def _flood_fill(mask, rows, cols, start_r, start_c, visited):
    """4-connected flood fill on a binary mask. Returns list of (r, c)."""
    stack = [(start_r, start_c)]
    blob = []
    while stack:
        r, c = stack.pop()
        if visited[r][c]:
            continue
        visited[r][c] = True
        blob.append((r, c))
        if r > 0 and mask[r - 1][c] and not visited[r - 1][c]:
            stack.append((r - 1, c))
        if r < rows - 1 and mask[r + 1][c] and not visited[r + 1][c]:
            stack.append((r + 1, c))
        if c > 0 and mask[r][c - 1] and not visited[r][c - 1]:
            stack.append((r, c - 1))
        if c < cols - 1 and mask[r][c + 1] and not visited[r][c + 1]:
            stack.append((r, c + 1))
    return blob


def _find_blobs(mask, rows, cols):
    """Find all connected components in a binary mask. Returns list of blobs."""
    visited = [[False] * cols for _ in range(rows)]
    blobs = []
    for r in range(rows):
        for c in range(cols):
            if mask[r][c] and not visited[r][c]:
                blob = _flood_fill(mask, rows, cols, r, c, visited)
                blobs.append(blob)
    return blobs


def _blob_bounds(blob):
    """Return (min_r, min_c, max_r, max_c) bounding box."""
    min_r = min(r for r, c in blob)
    max_r = max(r for r, c in blob)
    min_c = min(c for r, c in blob)
    max_c = max(c for r, c in blob)
    return min_r, min_c, max_r, max_c


# ── ThermalSensor class ──────────────────────────────────────────────

class ThermalSensor:
    """Thermal camera presence detection for snarling.

    Thread-safe properties:
        present      – bool, True if a person-sized warm blob is detected
        proximity    – float 0.0–1.0, how close the person appears
        ambient_temp – float, estimated ambient temperature in °C
        last_update  – float, epoch timestamp of last successful frame
    """

    def __init__(self, on_presence_change=None, on_proximity_change=None, on_display_zone_change=None, on_frame_data=None):
        """
        Args:
            on_presence_change: callback(was_absent: bool, now_present: bool, ambient_temp: float)
                called when presence state changes (15-frame debounce)
            on_proximity_change: callback(old_zone: str, new_zone: str, proximity: float, ambient_temp: float)
                called when confirmed proximity zone changes (15-frame debounce)
            on_display_zone_change: callback(old_zone: str, new_zone: str, proximity: float, ambient_temp: float)
                called on fast zone transitions (2-frame debounce) for face/LED responsiveness
            on_frame_data: callback(blob_list: list, best_person: dict|None, ambient: float)
                called every frame at 4Hz with raw blob data for V2 pipeline
        """
        self._on_presence_change = on_presence_change
        self._on_proximity_change = on_proximity_change
        self._on_display_zone_change = on_display_zone_change
        self._on_frame_data = on_frame_data

        # Shared state protected by lock
        self._lock = threading.Lock()
        self._present = False
        self._proximity = 0.0
        self._ambient_temp = 0.0
        self._last_update = 0.0
        self._zone = ZONE_ABSENT
        self._last_change = 0.0  # epoch of last state change

        # Cache latest rotated frame for thermal view rendering
        self._latest_rotated = None
        self._latest_ambient = None

        # Confirmed presence debounce (30-frame, for callbacks/cascade)
        self._debounce_present_count = 0
        self._debounce_absent_count = 0

        # Display debounce (2-frame, for face/LED responsiveness)
        self._display_zone = ZONE_ABSENT
        self._display_proximity = 0.0
        self._display_present = False
        self._display_present_count = 0
        self._display_absent_count = 0

        # Thread control
        self._thread = None
        self._stop_event = threading.Event()

        # Hardware handle (set up in _init_sensor)
        self._mlx = None
        self._sensor_ready = False

    # ── Public thread-safe properties ──────────────────────────────

    @property
    def present(self) -> bool:
        with self._lock:
            return self._present

    @property
    def proximity(self) -> float:
        with self._lock:
            return self._proximity

    @property
    def ambient_temp(self) -> float:
        with self._lock:
            return self._ambient_temp

    @property
    def last_update(self) -> float:
        with self._lock:
            return self._last_update

    @property
    def is_running(self) -> bool:
        """Check if the thermal reader thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def latest_frame(self):
        """Return (rotated_frame, rows, cols) or None if no frame yet.
        Thread-safe: returns a copy of the cached frame data."""
        with self._lock:
            if self._latest_rotated is None:
                return None
            return (list(self._latest_rotated), 32, 24)  # ROWS=32, COLS=24

    def get_presence_info(self) -> dict:
        """Return full presence info dict for /presence endpoint."""
        with self._lock:
            return {
                "present": self._present,
                "proximity": round(self._proximity, 2),
                "ambient_temp": round(self._ambient_temp, 1),
                "proximity_zone": self._zone,
                "display_zone": self._display_zone,
                "display_proximity": round(self._display_proximity, 2),
                "last_change": self._last_change,
                "last_update": self._last_update,
                "sensor_active": self._sensor_ready,
            }

    # ── Start / Stop ───────────────────────────────────────────────

    def start(self):
        """Start the thermal reading thread. No-op if sensor unavailable."""
        if self._thread is not None:
            logger.warning("ThermalSensor.start() called but thread already running")
            return

        if not self._init_sensor():
            # _init_sensor already logged why it failed
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                        name="thermal-reader")
        self._thread.start()
        logger.info("ThermalSensor reader thread started")

    def stop(self):
        """Stop the thermal reading thread."""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=3.0)
        self._thread = None
        logger.info("ThermalSensor reader thread stopped")

    # ── Sensor initialisation (called from start) ─────────────────

    def _init_sensor(self) -> bool:
        """Try to initialise the MLX90640. Returns True on success."""
        try:
            import board
            import busio
            from adafruit_mlx90640 import MLX90640, RefreshRate
        except ImportError as exc:
            logger.info("ThermalSensor disabled — MLX90640 library not available (%s)", exc)
            return False

        try:
            i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
            self._mlx = MLX90640(i2c, address=0x33)
            self._mlx.refresh_rate = RefreshRate.REFRESH_4_HZ
            # Do a throwaway read to confirm the sensor responds
            frame = [0.0] * 768
            self._mlx.getFrame(frame)
            self._sensor_ready = True
            logger.info("MLX90640 thermal camera initialised successfully")
            return True
        except Exception as exc:
            logger.info("ThermalSensor disabled — MLX90640 not found or I2C error (%s)", exc)
            return False

    # ── Main reader loop (runs in daemon thread) ──────────────────

    def _reader_loop(self):
        """Read thermal frames and update presence state."""
        # Pre-allocate the frame buffer once (768 floats)
        frame = [0.0] * 768
        consecutive_errors = 0

        while not self._stop_event.is_set():
            try:
                try:
                    self._mlx.getFrame(frame)
                except Exception as exc:
                    logger.warning("MLX90640 read error: %s — backing off %ss",
                                    exc, ERROR_BACKOFF)
                    with self._lock:
                        self._sensor_ready = False
                    self._stop_event.wait(ERROR_BACKOFF)
                    continue

                try:
                    now = time.time()
                    self._process_frame(frame, now)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    logger.error("Thermal frame processing error (#%d): %s", consecutive_errors, exc, exc_info=True)
                    if consecutive_errors >= 10:
                        logger.error("Too many consecutive processing errors — stopping thermal thread")
                        with self._lock:
                            self._sensor_ready = False
                        return
                    # Back off briefly before retrying
                    self._stop_event.wait(1.0)
                    continue

            except Exception as exc:
                # Catch-all for anything unexpected — don't let the thread die silently
                logger.error("Unexpected error in thermal reader loop: %s", exc, exc_info=True)
                self._stop_event.wait(ERROR_BACKOFF)

            # Sleep until next frame (aim for ~2 Hz)
            self._stop_event.wait(READ_INTERVAL)

    # ── Frame processing ──────────────────────────────────────────

    def _process_frame(self, frame, timestamp):
        """Analyse one thermal frame and update shared state."""
        # Raw sensor dimensions (camera mounted 90° CW)
        RAW_ROWS, RAW_COLS = 24, 32
        # After 90° CCW rotation to correct orientation
        ROWS, COLS = 32, 24

        # 0. Rotate frame 90° CCW to match physical orientation
        #    Camera is mounted 90° CW, so we undo it with 90° CCW.
        #    rotated[r][c] = raw[c][(RAW_COLS-1)-r]
        rotated = [0.0] * (ROWS * COLS)
        for r in range(ROWS):
            for c in range(COLS):
                raw_r = c
                raw_c = (RAW_COLS - 1) - r
                rotated[r * COLS + c] = frame[raw_r * RAW_COLS + raw_c]

        # Cache rotated frame for thermal view rendering
        with self._lock:
            self._latest_rotated = rotated

        # 1. Compute ambient using median of interior pixels (robust to edge
        #    artifacts and warm blobs). Edge margin pixels are excluded.
        interior_temps = []
        for r in range(EDGE_MARGIN, ROWS - EDGE_MARGIN):
            row_offset = r * COLS
            for c in range(EDGE_MARGIN, COLS - EDGE_MARGIN):
                interior_temps.append(rotated[row_offset + c])
        interior_temps.sort()
        ambient = interior_temps[len(interior_temps) // 2]  # median

        # 2. Threshold (adaptive — lower when ambient is high to reduce false negatives)
        threshold = ambient + person_delta(ambient)

        # 3. Binary mask — exclude edge pixels to avoid MLX90640 artifacts
        mask = [[False] * COLS for _ in range(ROWS)]
        for r in range(EDGE_MARGIN, ROWS - EDGE_MARGIN):
            row_offset = r * COLS
            for c in range(EDGE_MARGIN, COLS - EDGE_MARGIN):
                if rotated[row_offset + c] > threshold:
                    mask[r][c] = True

        # 4. Find blobs
        blobs = _find_blobs(mask, ROWS, COLS)

        # 5. Evaluate blobs for personhood
        best_person = None
        best_score = 0.0  # higher = more likely person / closer

        for blob in blobs:
            size = len(blob)
            min_r, min_c, max_r, max_c = _blob_bounds(blob)
            height = max_r - min_r + 1
            width = max_c - min_c + 1

            # Size check
            if size < MIN_PERSON_PIXELS:
                continue  # too small, skip

            # Aspect ratio checks
            if width == 0 or height == 0:
                continue  # degenerate blob
            aspect = width / height
            if aspect < MIN_BLOB_ASPECT:
                continue  # too narrow (tall thin strip — edge artifact)
            if width > height and width > height * 2:
                continue  # too horizontal, skip

            # Average temperature of the blob
            avg_temp = sum(rotated[r * COLS + c] for r, c in blob) / size

            # Proximity score: bigger + hotter = closer
            # size/40: a person at 3ft is ~55-65 pixels → size_factor ≈ 1.0
            # temp/5: a person at 3ft is ~4°C above ambient → temp_factor ≈ 0.8
            # Result: normal distance scores ~0.85-0.92 → solid "present"
            size_factor = min(size / 40.0, 1.0)
            temp_factor = min((avg_temp - ambient) / 5.0, 1.0)
            score = 0.5 * size_factor + 0.5 * max(temp_factor, 0.0)

            if score > best_score:
                best_score = score
                best_person = {
                    "size": size,
                    "avg_temp": avg_temp,
                    "height": height,
                    "width": width,
                    "score": score,
                }




        # 6. Determine raw presence and proximity
        if best_person is not None:
            raw_present = True
            # Clamp proximity: minimum 0.3 when a person is detected
            raw_proximity = max(0.3, min(best_person["score"], 1.0))
        else:
            raw_present = False
            raw_proximity = 0.0

        # 7. Fire frame data callback (4Hz, no debounce — for V2 pipeline)
        if self._on_frame_data:
            # Build blob list with V2-compatible format
            v2_blobs = []
            for blob_pixels in blobs:
                if len(blob_pixels) < MIN_PERSON_PIXELS:
                    continue  # skip tiny blobs
                size = len(blob_pixels)
                avg_temp = sum(rotated[r * COLS + c] for r, c in blob_pixels) / size
                min_r, min_c, max_r, max_c = _blob_bounds(blob_pixels)
                centroid_r = sum(r for r, c in blob_pixels) / size
                centroid_c = sum(c for r, c in blob_pixels) / size
                v2_blobs.append({
                    "centroid": (centroid_r, centroid_c),
                    "pixel_count": size,
                    "temp_min": min(rotated[r * COLS + c] for r, c in blob_pixels),
                    "temp_max": max(rotated[r * COLS + c] for r, c in blob_pixels),
                    "temp_mean": avg_temp,
                    "bbox": (min_r, min_c, max_r, max_c),
                    "width": max_c - min_c + 1,
                    "height": max_r - min_r + 1,
                    "area_pixels": size,
                    "aspect_ratio": (max_c - min_c + 1) / max(1, max_r - min_r + 1),
                })
            try:
                self._on_frame_data(v2_blobs, best_person, ambient)
            except Exception:
                logger.debug("frame_data callback error", exc_info=True)

        # 8. Debounce
        self._apply_debounce(raw_present, raw_proximity, ambient, timestamp)

    # ── Debounce logic ────────────────────────────────────────────

    def _apply_debounce(self, raw_present, raw_proximity, ambient, timestamp):
        """Apply hysteresis/debouncing before committing state changes.

        Two paths:
        - Display path (2-frame debounce): feeds face, LED, display_zone — responsive.
        - Confirmed path (30-frame debounce): feeds presence callbacks, cascade, logging — stable.
        """
        # ── Display path: fast debounce for face/LED ──────────────────────
        if raw_present:
            self._display_present_count += 1
            self._display_absent_count = 0
        else:
            self._display_absent_count += 1
            self._display_present_count = 0

        new_display_present = self._display_present
        if raw_present and not self._display_present:
            if self._display_present_count >= DEBOUNCE_FRAMES:
                new_display_present = True
        elif not raw_present and self._display_present:
            if self._display_absent_count >= DEBOUNCE_FRAMES:
                new_display_present = False

        display_zone = _proximity_to_zone(raw_proximity if new_display_present else 0.0)
        display_proximity = raw_proximity if new_display_present else 0.0

        fire_display_zone = False
        old_display_zone = None

        with self._lock:
            if display_zone != self._display_zone:
                old_display_zone = self._display_zone
                self._display_zone = display_zone
                fire_display_zone = True
            self._display_proximity = display_proximity
            self._display_present = new_display_present

        if fire_display_zone and self._on_display_zone_change and old_display_zone is not None:
            try:
                self._on_display_zone_change(old_display_zone, display_zone, display_proximity, ambient)
            except Exception:
                logger.debug("display_zone_change callback error", exc_info=True)

        # ── Confirmed path: slow debounce for presence callbacks ──────────
        with self._lock:
            current_present = self._present

        if raw_present:
            self._debounce_present_count += 1
            self._debounce_absent_count = 0
        else:
            self._debounce_absent_count += 1
            self._debounce_present_count = 0

        # Determine if we should flip presence
        new_present = current_present
        if raw_present and not current_present:
            if self._debounce_present_count >= PRESENCE_DEBOUNCE_FRAMES:
                new_present = True
        elif not raw_present and current_present:
            if self._debounce_absent_count >= PRESENCE_DEBOUNCE_FRAMES:
                new_present = False

        # Determine proximity zone
        new_zone = _proximity_to_zone(raw_proximity if new_present else 0.0)

        # Fire callbacks if needed (outside the lock to avoid deadlock risk)
        fire_presence = False
        fire_proximity = False
        old_zone = None

        with self._lock:
            # Update ambient and timestamp always
            self._ambient_temp = ambient
            self._last_update = timestamp

            # Update presence
            if new_present != self._present:
                was_absent = not self._present
                self._present = new_present
                self._last_change = timestamp
                fire_presence = True

            # Update proximity
            if new_present:
                self._proximity = raw_proximity
            else:
                self._proximity = 0.0

            # Check zone change
            if new_zone != self._zone:
                old_zone = self._zone
                self._zone = new_zone
                self._last_change = timestamp
                fire_proximity = True

        # Fire callbacks outside the lock
        if fire_presence and self._on_presence_change:
            try:
                self._on_presence_change(not new_present, new_present, ambient)
            except Exception:
                logger.debug("presence_change callback error", exc_info=True)

        if fire_proximity and self._on_proximity_change and old_zone is not None:
            try:
                self._on_proximity_change(old_zone, new_zone, self.proximity, ambient)
            except Exception:
                logger.debug("proximity_change callback error", exc_info=True)


# ── Helper ────────────────────────────────────────────────────────

def _proximity_to_zone(proximity: float) -> str:
    if proximity <= 0.0:
        return ZONE_ABSENT
    elif proximity < 0.65:
        return ZONE_APPROACHING
    else:
        return ZONE_PRESENT


# ── Convenience: try to create a sensor, return None if unavailable ──

def create_thermal_sensor(on_presence_change=None, on_proximity_change=None, on_display_zone_change=None):
    """Factory that returns a ThermalSensor if hardware is available, else None.

    This is the preferred way for snarling to get a thermal sensor instance.
    If the MLX90640 library or hardware isn't present, returns None and logs
    a single info message.
    """
    sensor = ThermalSensor(
        on_presence_change=on_presence_change,
        on_proximity_change=on_proximity_change,
        on_display_zone_change=on_display_zone_change,
    )
    # _init_sensor is called inside start(), but we check early here too
    # so callers can decide whether to even bother.
    # The real init happens in start(), which is idempotent on failure.
    return sensor