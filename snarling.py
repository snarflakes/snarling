
#!/usr/bin/env python3
"""
snarling-style Creature for DisplayHAT Mini
Screen: 320x240, rotated 180 degrees
"""

from displayhatmini import DisplayHATMini
from PIL import Image, ImageDraw, ImageFont
import time
import math
import random
import signal
import sys
import json
import threading

# Fix output buffering so print statements show up immediately in logs
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Import OpenClaw integration
# OpenClaw polling client removed — state is now set via direct /state API from the plugin
OPENCLAW_AVAILABLE = False

# Screen dimensions
WIDTH = DisplayHATMini.WIDTH
HEIGHT = DisplayHATMini.HEIGHT

# States
STATE_SLEEPING = "sleeping"
STATE_PROCESSING = "processing"
STATE_COMMUNICATING = "communicating"
STATE_ERROR = "error"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_NOTIFYING = "notifying"

# Color constants
COLOR_BG = (26, 26, 46)       # Deep charcoal #1A1A2E
COLOR_TEXT = (255, 255, 255)
COLOR_SLEEP = (100, 150, 255)
COLOR_PROCESS = (255, 168, 148)  # Light melon
COLOR_COMM = (0, 255, 220)
COLOR_ERROR = (255, 80, 80)

# New design system colors
COLOR_BG_NEW = (26, 26, 46)        # Deep charcoal #1A1A2E
COLOR_INNER_FRAME = (255, 255, 255) # White inner frame
COLOR_BUTTON_INDICATOR = (80, 80, 100)  # Soft grey for button hints
COLOR_STATUS_BOX = (255, 255, 255)  # White status boxes
COLOR_STATUS_DIM = (60, 60, 80)    # Dim/off status boxes
COLOR_BANNER_BG = (42, 42, 62)    # Slightly lighter dark for banner area #2A2A3E
COLOR_SEPARATOR = (255, 255, 255)  # White separator line

# Design system dimensions
BORDER_MARGIN = 5             # Outer border margin from screen edges
BORDER_RADIUS = 18            # Outer border corner radius
BORDER_WIDTH = 5              # Outer border stroke width
INNER_FRAME_INSET = 10        # Inner frame inset from outer border
INNER_FRAME_WIDTH = 1         # Inner frame stroke width
BUTTON_W = 20                 # Button indicator width
BUTTON_H = 14                 # Button indicator height
BUTTON_RADIUS = 4             # Button indicator corner radius
BUTTON_MARGIN = 8             # Button indicator margin from inner frame
STATUS_BOX_SIZE = 8           # Status box size (px)
STATUS_BOX_GAP = 4            # Gap between status boxes
STATUS_BOX_Y = 0              # Will be calculated relative to top
BANNER_HEIGHT = 80            # Banner area height when visible

# Notification LED colors by priority
NOTIFY_LED_COLORS = {
    'high': (0.5, 0.15, 0.0),      # Red-orange (50%)
    'normal': (0.5, 0.3, 0.0),     # Yellow-orange (50%)
    'low': (0.5, 0.5, 0.0),        # Yellow (50%)
}

# Pwnagotchi-style ASCII face expressions with animations

class FaceExpressions:
    """Pwnagotchi-style Unicode face expressions"""

    # Sleeping faces - calm, resting
    SLEEP = ['(⇀‿‿↼)', '(≖‿‿≖)']

    # Processing faces - focused, working
    PROCESSING = ['(◕‿‿◕)', '(•‿‿•)', '(-__-)', '(✜‿‿✜)']

    # Communicating faces - excited, talking
    COMMUNICATING = ['(ᵔ◡◡ᵔ)', '(°▃▃°)', '(⌐■_■)', '(☼‿‿☼)']

    # Error faces - distressed, broken
    ERROR = ['(╥☁╥ )', '(-_-\')', '(☓‿‿☓)', '(#__#)']

    # Awaiting approval faces - alert, watching
    AWAITING_APPROVAL = ['( ⚆_⚆)', '(☉_☉ )']

    # Notification faces - priority-based
    # High: urgent, demanding attention
    NOTIFY_HIGH = ['(☉_☉)', '(ಠ_ಠ)', '(⚠_⚠)']
    # Normal: informative, curious, aware
    NOTIFY_NORMAL = ['(•_•)', '(◡_◡)', '(⊙_⊙)', '(◉_◉)']
    # Low: gentle, slight perk, relaxed
    NOTIFY_LOW = ['(◠‿◠)']

    # Proximity-aware faces (used when sleeping + someone detected)
    PROXIMITY_APPROACHING = ['(⊙◡⊙)', '(☉.☉)']  # Eyes widening — awareness, slight rotation
    PROXIMITY_PRESENT = ['(◠‿◠)', '(◕‿◕)', '(◐‿◐)']  # Warm → eager → awkward — raw presence cycle
    GRATEFUL = ['(^‿‿^)']              # Joyful appreciation
    LEAVING = ['(◡‿◡)']                  # Brief "goodbye" face when someone walks away

    @classmethod
    def get_faces_for_state(cls, state, priority=None):
        """Get appropriate faces for a given state"""
        if state == STATE_SLEEPING:
            return cls.SLEEP
        elif state == STATE_PROCESSING:
            return cls.PROCESSING
        elif state == STATE_COMMUNICATING:
            return cls.COMMUNICATING
        elif state == STATE_ERROR:
            return cls.ERROR
        elif state == STATE_AWAITING_APPROVAL:
            return cls.AWAITING_APPROVAL
        elif state == STATE_NOTIFYING:
            return cls.get_notify_faces(priority or 'normal')
        return cls.SLEEP

    @classmethod
    def get_notify_faces(cls, priority):
        """Get notification faces for a given priority level"""
        if priority == 'high':
            return cls.NOTIFY_HIGH
        elif priority == 'normal':
            return cls.NOTIFY_NORMAL
        elif priority == 'low':
            return cls.NOTIFY_LOW
        return cls.NOTIFY_NORMAL

class snarlingCreature:
    """Main creature class"""

    def __init__(self):
        self.state = STATE_SLEEPING
        self.mute = False
        self.last_update = time.time()
        self.breath_phase = 0.0
        self.think_dots = 0
        self.talk_frame = 0
        self.running = True

        # Approval resolution counters
        self.approval_counts = {"approved": 0, "rejected": 0}
        self.status_message = ""
        self.status_timer = 0

        # Screen sleep mode flag
        self.screen_asleep = False

        # Face animation attributes
        self.current_face = "(◕‿‿◕)"
        self.face_index = 0
        self.face_timer = 0
        self.animation_offset_x = 0
        self.animation_offset_y = 0

        # Notification state
        self._notify_active = False
        self._notify_priority = 'normal'
        self._notify_message = ''
        self._notify_start_time = 0
        self._notify_text_revealed = False
        self._notify_pre_state = STATE_SLEEPING  # state to return to after notification
        self._notify_showing_notify_face = False  # True when current face is a notification face
        self._notify_id = None           # notification_id from plugin
        self._notify_callback_url = None  # where to POST feedback
        self._notify_session_key = None   # session routing
        self._notify_secret = None        # auth secret (same APPROVAL_SECRET)
        self._notify_duration = 0         # auto-dismiss timeout in seconds (0 = use priority-based default: low=300s, others=no timeout)
        self._notify_sent_time = 0       # when the notification was sent (epoch) — for computing total time-to-reveal including queue

        # Notification stack (priority-sorted pending queue)
        self._notify_stack = []  # list of {"message": str, "priority": str, "_seq": int, "notification_id": str|None, "callback_url": str|None, "session_key": str|None, "secret": str|None, "duration": int}
        self._notify_seq = 0  # monotonically increasing insertion counter for LIFO within same priority

        # Banner cycling (may already exist via set_notification, but init here too)
        self._notify_banners = []
        self._notify_banner_index = 0
        self._notify_banner_timer = 0
        self._notify_banner_interval = 45

        # LED brightness for breathing (0-1)
        self.led_brightness = 0.5

        # LED timer for state change indication
        self.led_timer = 0

        # ── Thermal sensor integration ──────────────────────────
        self.thermal = None
        self._thermal_available = False

        # Environmental state (merges thermal + external data)
        self._environmental_state = {
            "present": False,
            "proximity": 0.0,
            "proximity_zone": "absent",
            "source": "none",
            "last_change": None,
            "ambient_temp": None,
        }
        self._environmental_lock = threading.Lock()

        # Proximity-driven brightness transitions
        self._brightness_target = 0.2
        self._brightness_current = 0.2
        self._brightness_ramp_start = 0
        self._brightness_ramp_duration = 0.7

        # Proximity-driven face state (only used when agent state is sleeping)
        self._proximity_face_pending = None
        self._proximity_face_time = 0
        self._proximity_face_current = "(≖◡◡≖)"

        # LED hysteresis: require stable presence before LED turns on
        # Prevents LED flickering when proximity bounces near thresholds
        self._led_present_counter = 0    # consecutive frames with presence
        self._led_absent_counter = 0     # consecutive frames without presence
        self._led_confirmed_present = False  # LED only uses presence after stable

        # Walking away face
        self._leaving_face_active = False
        self._leaving_face_timer = 0.0

        # Thermal health check counter
        self._thermal_health_counter = 0

        # Track previous presence state for leaving-face detection
        self._prev_env_present = False

        # ── End thermal integration ───────────────────────────────




        # Initialize display
        self.img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG_NEW)
        self.draw = ImageDraw.Draw(self.img)
        self.display = DisplayHATMini(self.img)

        # Set initial LED
        self.update_led()

        # Initialize thermal sensor (after display is set up)
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

        # Button handlers
        self.button_pressed = {
            'A': False,
            'B': False,
            'X': False,
            'Y': False
        }

        # Setup signal handlers for clean exit
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.running = False

    def update_led(self):
        """Update LED based on state, breathing animation, and proximity.
        State sets the LED color/pattern. Proximity adds a warm glow on top.
        When sleeping with no active state LED, proximity drives the LED fully."""
        # Turn off LED when screen is asleep
        if self.screen_asleep:
            self.display.set_led(0, 0, 0)
            return

        # Get current environmental state (thread-safe)
        try:
            with self._environmental_lock:
                env_present = self._environmental_state["present"]
                env_proximity = self._environmental_state["proximity"]
        except Exception:
            env_present = False
            env_proximity = 0.0

        # LED hysteresis: require stable presence for a few frames before reacting
        # This prevents LED flickering when proximity bounces near thresholds
        LED_HYSTERESIS_FRAMES = 3  # frames to confirm presence/absence

        if env_present:
            self._led_present_counter += 1
            self._led_absent_counter = 0
            if self._led_present_counter >= LED_HYSTERESIS_FRAMES:
                self._led_confirmed_present = True
        else:
            self._led_absent_counter += 1
            self._led_present_counter = 0
            if self._led_absent_counter >= LED_HYSTERESIS_FRAMES:
                self._led_confirmed_present = False

        # Use confirmed presence for LED decisions (with proximity value)
        led_present = self._led_confirmed_present and env_proximity > 0.1

        # Calculate proximity brightness using exponential smoothing
        frame_dt = 1.0 / 30.0  # ~30fps
        alpha = 1 - math.exp(-frame_dt / 0.23)  # ~0.7s time constant

        self._brightness_current += (self._brightness_target - self._brightness_current) * alpha
        # Clamp overshoot after settling
        if self._brightness_target <= 1.0 and self._brightness_current > 1.0:
            overshoot_alpha = 1 - math.exp(-frame_dt / 0.067)  # ~200ms settle
            self._brightness_current += (1.0 - self._brightness_current) * overshoot_alpha

        proximity_brightness = max(0.15, min(self._brightness_current, 1.0))

        # Determine base LED color from state
        if self.led_timer > 0 or self.state in (STATE_PROCESSING, STATE_COMMUNICATING, STATE_ERROR, STATE_AWAITING_APPROVAL, STATE_NOTIFYING):
            # State-driven LED colors
            if self.state == STATE_SLEEPING:
                brightness = 0.3 + 0.2 * math.sin(self.breath_phase)
                brightness *= 0.7
                base_r, base_g, base_b = 0, brightness * 0.25, brightness * 0.5
            elif self.state == STATE_PROCESSING:
                pulse = 0.3 + 0.25 * math.sin(self.breath_phase * 1.5)
                pulse *= 0.7
                base_r, base_g, base_b = pulse * 0.99, pulse * 0.54, pulse * 0.45
            elif self.state == STATE_COMMUNICATING:
                pulse = 0.5 + 0.5 * math.sin(self.breath_phase * 2)
                pulse *= 0.7
                base_r, base_g, base_b = 0, pulse, pulse
            elif self.state == STATE_ERROR:
                blink = 1.0 if int(self.breath_phase * 3) % 2 == 0 else 0.4
                blink *= 0.7
                base_r, base_g, base_b = blink, 0, 0
            elif self.state == STATE_AWAITING_APPROVAL:
                blink = 1.0 if int(self.breath_phase * 4) % 2 == 0 else 0.2
                blink *= 0.7
                base_r, base_g, base_b = blink, 0, 0
            elif self.state == STATE_NOTIFYING:
                if self._notify_showing_notify_face:
                    led_color = NOTIFY_LED_COLORS.get(self._notify_priority, NOTIFY_LED_COLORS['normal'])
                    flash_rates = {'high': 6, 'normal': 3, 'low': 1.5}
                    rate = flash_rates.get(self._notify_priority, 3)
                    blink = 1.0 if int(self.breath_phase * rate) % 2 == 0 else 0.3
                    blink *= 0.7
                    base_r = led_color[0] * blink
                    base_g = led_color[1] * blink
                    base_b = led_color[2] * blink
                else:
                    base_r, base_g, base_b = 0, 0, 0
            else:
                base_r, base_g, base_b = 0, 0, 0

            # Composite: add a warm proximity glow on top of the state LED
            # Makes even processing/communicating feel slightly different when you're nearby
            if self._thermal_available and led_present and env_proximity > 0.1:
                warmth_mix = env_proximity * 0.3  # subtle — 30% max mix
                base_r = min(1.0, base_r + warmth_mix * 0.5)   # warm red
                base_g = min(1.0, base_g + warmth_mix * 0.15)  # tiny green

            self.display.set_led(min(1.0, max(0.0, base_r)), min(1.0, max(0.0, base_g)), min(1.0, max(0.0, base_b)))

        elif self._thermal_available and led_present:
            # Sleeping (no state LED timer) + person present: proximity drives LED fully
            pulse = 0.3 + 0.4 * math.sin(self.breath_phase * 0.8) * env_proximity
            warmth = env_proximity
            self.display.set_led(
                min(1.0, max(0.0, pulse * warmth * 0.8)),
                min(1.0, max(0.0, pulse * 0.3)),
                min(1.0, max(0.0, pulse * (1 - warmth) * 0.5))
            )
        elif self.led_timer <= 0:
            # No state LED timer and no proximity — gentle breathing or off
            if self.state == STATE_SLEEPING:
                brightness = 0.3 + 0.2 * math.sin(self.breath_phase)
                brightness *= 0.7
                self.display.set_led(0, min(1.0, max(0.0, brightness * 0.25)), min(1.0, max(0.0, brightness * 0.5)))
            else:
                self.display.set_led(0, 0, 0)

    def get_color(self):
        """Get current color based on state (and face type during notifications)"""
        if self.state == STATE_NOTIFYING:
            # When showing a notification face, use notification color;
            # when showing the normal pre-state face, use that state's color
            if self._notify_showing_notify_face:
                notify_colors = {
                    'high': (255, 140, 50),    # warm red-orange
                    'normal': (255, 200, 80),   # yellow-orange
                    'low': (200, 200, 100),      # soft yellow
                }
                return notify_colors.get(self._notify_priority, notify_colors['normal'])
            else:
                # Return the color for whatever state we were in before the notification
                pre_colors = {
                    STATE_SLEEPING: COLOR_SLEEP,
                    STATE_PROCESSING: COLOR_PROCESS,
                    STATE_COMMUNICATING: COLOR_COMM,
                    STATE_ERROR: COLOR_ERROR,
                }
                return pre_colors.get(self._notify_pre_state, COLOR_SLEEP)
        colors = {
            STATE_SLEEPING: COLOR_SLEEP,
            STATE_PROCESSING: COLOR_PROCESS,
            STATE_COMMUNICATING: COLOR_COMM,
            STATE_ERROR: COLOR_ERROR,
            STATE_AWAITING_APPROVAL: COLOR_ERROR  # Red for approval alert
        }
        return colors.get(self.state, COLOR_SLEEP)

    def get_current_face(self):
        """Get current face expression"""
        return self.current_face

    def update_face(self, dt):
        """Update face animation and expression"""
        # Update face timer for expression changes
        self.face_timer += dt

        # Update leaving face timer
        if self._leaving_face_active:
            self._leaving_face_timer -= dt
            if self._leaving_face_timer <= 0:
                self._leaving_face_active = False

        # Check for pending proximity-driven face transition (with delay)
        now = time.time()
        if self._proximity_face_pending and now >= self._proximity_face_time:
            self._proximity_face_current = self._proximity_face_pending
            self._proximity_face_pending = None

        if self.face_timer > 2.0:  # Change face every 2 seconds for more variety
            # Leaving face overrides everything (brief goodbye)
            if self._leaving_face_active:
                faces = FaceExpressions.LEAVING
            elif self._thermal_available and self.state == STATE_SLEEPING:
                # Proximity drives face when sleeping
                try:
                    with self._environmental_lock:
                        zone = self._environmental_state["proximity_zone"]
                except Exception:
                    zone = "absent"

                if zone == "present":
                    faces = FaceExpressions.PROXIMITY_PRESENT
                elif zone == "approaching":
                    faces = FaceExpressions.PROXIMITY_APPROACHING
                else:
                    faces = FaceExpressions.SLEEP  # Dozing
            elif self.state == STATE_NOTIFYING and self._notify_active:
                # Frequency-mixed face rotation: alternate between notification faces
                # and the previous state's normal faces based on priority
                notify_faces = FaceExpressions.get_notify_faces(self._notify_priority)
                pre_faces = FaceExpressions.get_faces_for_state(self._notify_pre_state)
                # Priority-based probability of showing notification face vs pre-state face
                notify_probs = {'high': 0.8, 'normal': 0.4, 'low': 0.2}
                prob = notify_probs.get(self._notify_priority, 0.4)
                if random.random() < prob:
                    faces = notify_faces
                    self._notify_showing_notify_face = True
                else:
                    faces = pre_faces
                    self._notify_showing_notify_face = False
            else:
                faces = FaceExpressions.get_faces_for_state(self.state, getattr(self, '_notify_priority', None))
            if faces:
                self.face_index = (self.face_index + 1) % len(faces)
                self.current_face = faces[self.face_index]
            self.face_timer = 0

        # Override current face with leaving face if active (instant, not waiting for timer)
        if self._leaving_face_active:
            self.current_face = FaceExpressions.LEAVING[0]
        # Override with pending proximity face if it's time (only when someone is present or approaching)
        elif self._proximity_face_pending is None and self._thermal_available and self.state == STATE_SLEEPING:
            try:
                with self._environmental_lock:
                    zone = self._environmental_state["proximity_zone"]
            except Exception:
                zone = "absent"
            # Proximity face cycling is handled by the face list rotation above;
            # no static override needed for any zone

        # Update animation offsets based on state
        if self.state == STATE_SLEEPING:
            # Slow bobbing up and down
            self.animation_offset_y = int(8 * math.sin(self.breath_phase * 0.5))
            self.animation_offset_x = 0
        elif self.state == STATE_PROCESSING:
            # Tilting left and right
            self.animation_offset_x = int(6 * math.sin(self.breath_phase * 1.5))
            self.animation_offset_y = 0
        elif self.state == STATE_COMMUNICATING:
            # Fast up and down
            self.animation_offset_y = int(4 * math.sin(self.breath_phase * 3))
            self.animation_offset_x = 0
        elif self.state == STATE_ERROR:
            # Side to side movement
            self.animation_offset_x = int(10 * math.sin(self.breath_phase * 2))
            self.animation_offset_y = 0
        elif self.state == STATE_AWAITING_APPROVAL:
            # Alert movement - quick jitter
            self.animation_offset_x = int(4 * math.sin(self.breath_phase * 6))
            self.animation_offset_y = int(2 * math.cos(self.breath_phase * 5))
        elif self.state == STATE_NOTIFYING:
            if self._notify_priority == 'high':
                # Quick jitter (like awaiting_approval)
                self.animation_offset_x = int(4 * math.sin(self.breath_phase * 6))
                self.animation_offset_y = int(2 * math.cos(self.breath_phase * 5))
            elif self._notify_priority == 'normal':
                # Slow side-to-side look
                self.animation_offset_x = int(8 * math.sin(self.breath_phase * 1.0))
                self.animation_offset_y = 0
            else:
                # low: gentle bob (like sleeping but slightly faster)
                self.animation_offset_y = int(6 * math.sin(self.breath_phase * 0.8))
                self.animation_offset_x = 0

    def _is_banner_active(self):
        """Check if a banner (notification, approval, or status message) is currently active and visible.
        For notifications, the banner only counts as active when the user has pressed A to reveal text.
        Before reveal, only a subtle hint is shown — no banner background or face shift."""
        if self.state == STATE_NOTIFYING and self._notify_active:
            return self._notify_text_revealed
        return (
            (self.state == STATE_AWAITING_APPROVAL and hasattr(self, '_approval_banners')) or
            self.status_timer > 0
        )

    def draw_background(self):
        """Fill background — slightly brighter when someone is present"""
        try:
            with self._environmental_lock:
                env_proximity = self._environmental_state["proximity"]
        except Exception:
            env_proximity = 0.0

        # Interpolate between deep dark (absent) and slightly less dark (present)
        r = int(26 + env_proximity * 10)   # 26 → 36
        g = int(26 + env_proximity * 10)    # 26 → 36
        b = int(46 + env_proximity * 8)    # 46 → 54
        self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(r, g, b))

    def draw_outer_border(self):
        """Draw the mood-colored rounded rectangle outer border"""
        color = self.get_color()
        # Outer border: rounded rectangle with thick stroke
        self.draw.rounded_rectangle(
            (BORDER_MARGIN, BORDER_MARGIN, WIDTH - BORDER_MARGIN, HEIGHT - BORDER_MARGIN),
            radius=BORDER_RADIUS,
            outline=color,
            width=BORDER_WIDTH
        )

    def draw_inner_frame(self):
        """Draw thin white inner frame for double-border effect"""
        inset = BORDER_MARGIN + INNER_FRAME_INSET
        self.draw.rounded_rectangle(
            (inset, inset, WIDTH - inset, HEIGHT - inset),
            radius=BORDER_RADIUS - 2,
            outline=COLOR_INNER_FRAME,
            width=INNER_FRAME_WIDTH
        )

    def draw_button_indicators(self):
        """Draw 4 soft grey button indicators in corners: Y (top-left), B (top-right), X (bottom-left), A (bottom-right)
        Top two (Y, B) always visible.
        Bottom two (X, A) visible only when banner is hidden.
        """
        inset = BORDER_MARGIN + INNER_FRAME_INSET + 1  # Inside inner frame
        x_left = inset + BUTTON_MARGIN
        x_right = WIDTH - inset - BUTTON_MARGIN - BUTTON_W
        y_top = inset + BUTTON_MARGIN
        y_bottom = HEIGHT - inset - BUTTON_MARGIN - BUTTON_H

        # Button label font (tiny)
        try:
            btn_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 10
            )
        except OSError:
            btn_font = ImageFont.load_default()

        # Top-left: Y (always visible)
        self.draw.rounded_rectangle(
            (x_left, y_top, x_left + BUTTON_W, y_top + BUTTON_H),
            radius=BUTTON_RADIUS,
            fill=COLOR_BUTTON_INDICATOR
        )
        # Label
        lbl_bbox = self.draw.textbbox((0, 0), "Y", font=btn_font)
        lbl_w = lbl_bbox[2] - lbl_bbox[0]
        lbl_h = lbl_bbox[3] - lbl_bbox[1]
        self.draw.text(
            (x_left + (BUTTON_W - lbl_w) // 2, y_top + (BUTTON_H - lbl_h) // 2 - 1),
            "Y", fill=(200, 200, 220), font=btn_font
        )

        # Top-right: B (always visible)
        self.draw.rounded_rectangle(
            (x_right, y_top, x_right + BUTTON_W, y_top + BUTTON_H),
            radius=BUTTON_RADIUS,
            fill=COLOR_BUTTON_INDICATOR
        )
        lbl_bbox = self.draw.textbbox((0, 0), "B", font=btn_font)
        lbl_w = lbl_bbox[2] - lbl_bbox[0]
        lbl_h = lbl_bbox[3] - lbl_bbox[1]
        self.draw.text(
            (x_right + (BUTTON_W - lbl_w) // 2, y_top + (BUTTON_H - lbl_h) // 2 - 1),
            "B", fill=(200, 200, 220), font=btn_font
        )

        # Bottom-left: X (only when no banner)
        banner_active = (
            (self.state == STATE_NOTIFYING and self._notify_active) or
            (self.state == STATE_AWAITING_APPROVAL and hasattr(self, '_approval_banners')) or
            self.status_timer > 0
        )
        if not banner_active:
            self.draw.rounded_rectangle(
                (x_left, y_bottom, x_left + BUTTON_W, y_bottom + BUTTON_H),
                radius=BUTTON_RADIUS,
                fill=COLOR_BUTTON_INDICATOR
            )
            lbl_bbox = self.draw.textbbox((0, 0), "X", font=btn_font)
            lbl_w = lbl_bbox[2] - lbl_bbox[0]
            lbl_h = lbl_bbox[3] - lbl_bbox[1]
            self.draw.text(
                (x_left + (BUTTON_W - lbl_w) // 2, y_bottom + (BUTTON_H - lbl_h) // 2 - 1),
                "X", fill=(200, 200, 220), font=btn_font
            )

            # Bottom-right: A (only when no banner)
            self.draw.rounded_rectangle(
                (x_right, y_bottom, x_right + BUTTON_W, y_bottom + BUTTON_H),
                radius=BUTTON_RADIUS,
                fill=COLOR_BUTTON_INDICATOR
            )
            lbl_bbox = self.draw.textbbox((0, 0), "A", font=btn_font)
            lbl_w = lbl_bbox[2] - lbl_bbox[0]
            lbl_h = lbl_bbox[3] - lbl_bbox[1]
            self.draw.text(
                (x_right + (BUTTON_W - lbl_w) // 2, y_bottom + (BUTTON_H - lbl_h) // 2 - 1),
                "A", fill=(200, 200, 220), font=btn_font
            )

    def draw_status_boxes(self):
        """Draw 5 status squares at top center based on state:
        - Processing: fill progressively (1/5, 2/5, etc.)
        - Idle/communicating: all 5 solid white
        - Sleeping: all dim/off
        - Approval: all 5 solid red
        - Notification: fill based on priority count
        """
        inset = BORDER_MARGIN + INNER_FRAME_INSET + 1
        total_width = 5 * STATUS_BOX_SIZE + 4 * STATUS_BOX_GAP
        start_x = (WIDTH - total_width) // 2
        y = inset + 4  # Touching the white inner frame

        # Determine fill level and color
        if self.state == STATE_SLEEPING:
            fill_count = 0
            box_color = COLOR_STATUS_DIM
        elif self.state == STATE_PROCESSING:
            # Progressive fill: cycle through 1-5 based on animation
            fill_count = (self.face_index % 5) + 1
            box_color = COLOR_STATUS_BOX
        elif self.state == STATE_AWAITING_APPROVAL:
            fill_count = 5
            box_color = COLOR_ERROR  # Red
        elif self.state == STATE_NOTIFYING and self._notify_active:
            # Fill based on priority: high=5, normal=3, low=1
            priority_fill = {'high': 5, 'normal': 3, 'low': 1}
            fill_count = priority_fill.get(self._notify_priority, 3)
            notify_box_colors = {
                'high': (255, 140, 50),    # warm red-orange
                'normal': (255, 200, 80),   # yellow-orange
                'low': (200, 200, 100),      # soft yellow
            }
            box_color = notify_box_colors.get(self._notify_priority, (255, 200, 80))
        else:
            # Idle / communicating / error: all 5 solid
            fill_count = 5
            box_color = COLOR_STATUS_BOX

        for i in range(5):
            x = start_x + i * (STATUS_BOX_SIZE + STATUS_BOX_GAP)
            color = box_color if i < fill_count else COLOR_STATUS_DIM
            self.draw.rounded_rectangle(
                (x, y, x + STATUS_BOX_SIZE, y + STATUS_BOX_SIZE),
                radius=1,
                fill=color
            )

    def draw_separator(self):
        """Draw thin white horizontal line between face area and banner area.
        Only visible when banner is active."""
        inset = BORDER_MARGIN + INNER_FRAME_INSET + 1
        sep_y = HEIGHT - inset - BANNER_HEIGHT
        self.draw.line(
            (inset + 1, sep_y, WIDTH - inset - 1, sep_y),
            fill=COLOR_SEPARATOR,
            width=1
        )

    def draw_banner_background(self):
        """No filled background below separator — just the line."""
        pass

    def draw_face(self):
        """Draw the face expression in the center of the screen using DejaVuSansMono like pwnagotchi"""
        face = self.get_current_face()
        color = self.get_color()

        # Cache font lookup on first call - use DejaVuSansMono like pwnagotchi
        if not hasattr(self, '_cached_font'):
            self._cached_font_size = 62
            
            try:
                # Use DejaVuSansMono-Bold like pwnagotchi
                self._cached_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 
                    self._cached_font_size
                )
                print("Loaded DejaVuSansMono-Bold font")
            except Exception as e:
                # Fallback
                try:
                    self._cached_font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 
                        self._cached_font_size
                    )
                    print(f"Loaded DejaVuSansMono font (Bold not available)")
                except:
                    self._cached_font = ImageFont.load_default()
                    print("Warning: Using default font")

        font = self._cached_font
        
        # Create a larger canvas for high-quality rendering
        render_scale = 2
        render_size = self._cached_font_size * render_scale
        
        try:
            # Reload at larger size
            large_font = ImageFont.truetype(font.path, render_size)
        except:
            large_font = font
            render_scale = 1

        # Get text bounding box for centering
        bbox = self.draw.textbbox((0, 0), face, font=large_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Create temporary image for high-quality rendering
        padding = 20
        temp_width = text_width + padding * 2
        temp_height = text_height + padding * 2
        
        text_img = Image.new('RGBA', (temp_width, temp_height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_img)

        # Draw text with bbox offset to ensure full glyph visibility
        text_draw.text((padding - bbox[0], padding - bbox[1]), face, fill=color, font=large_font)

        # Scale down if we rendered large
        if render_scale > 1:
            final_size = (temp_width // render_scale, temp_height // render_scale)
            text_img = text_img.resize(final_size, Image.Resampling.LANCZOS)

        # Center position with animation offsets
        # Shift face up slightly to account for banner area at bottom
        face_y_offset = -20 if self._is_banner_active() else 0
        x = (WIDTH - text_img.width) // 2 + self.animation_offset_x
        y = (HEIGHT - text_img.height) // 2 + self.animation_offset_y + face_y_offset - 10

        # Paste with alpha blending
        if text_img.mode == 'RGBA':
            mask = text_img.split()[3]  # Alpha channel
            self.img.paste(text_img, (x, y), mask)
        else:
            self.img.paste(text_img, (x, y))

    def draw_status(self):
        """Draw status banners at bottom within the banner area.
        Banner backgrounds are drawn by draw_banner_background() called from draw_frame().
        This method only draws text and other overlay content."""
        inset = BORDER_MARGIN + INNER_FRAME_INSET + 1
        banner_top = HEIGHT - inset - BANNER_HEIGHT + 4  # Small padding from separator
        banner_bottom = HEIGHT - inset - 4  # Small padding from bottom
        text_left = inset + 10
        text_right = WIDTH - inset - 30
        max_text_width = text_right - text_left

        # Mute indicator
        if self.mute:
            self.draw.text((text_left, banner_bottom - 20), "🔇", fill=(150, 150, 150))

        # State indicator (only show when no active banner)
        if not self._is_banner_active():
            try:
                state_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 14
                )
            except OSError:
                state_font = ImageFont.load_default()
            state_text = f"{self.state.upper()}"
            bbox = self.draw.textbbox((0, 0), state_text, font=state_font)
            st_w = bbox[2] - bbox[0]
            # Center the state text horizontally
            st_x = (WIDTH - st_w) // 2
            self.draw.text((st_x, banner_bottom - 18), state_text, fill=(80, 80, 100), font=state_font)

        # Notification banners/hints
        if self.state == STATE_NOTIFYING and self._notify_active:
            try:
                notify_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19
                )
                hint_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18
                )
            except OSError:
                notify_font = ImageFont.load_default()
                hint_font = notify_font

            if self._notify_text_revealed:
                # Advance banner timer and swap (same pattern as approval banners)
                self._notify_banner_timer += 1
                if self._notify_banner_timer >= self._notify_banner_interval:
                    self._notify_banner_timer = 0
                    self._notify_banner_index = (self._notify_banner_index + 1) % len(self._notify_banners)

                try:
                    header_font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 24
                    )
                    msg_font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19
                    )
                except OSError:
                    header_font = ImageFont.load_default()
                    msg_font = header_font

                lines = self._notify_banners[self._notify_banner_index]
                is_banner1 = (self._notify_banner_index == 0)

                if is_banner1:
                    # Banner 1: header + preview (2 lines)
                    total_notifications = 1 + len(self._notify_stack)
                    if total_notifications > 1:
                        count_indicator = f" ({1}/{total_notifications})"
                        self.draw.text((text_left, banner_top), lines[0] + count_indicator, fill=(255, 200, 200), font=header_font)
                    else:
                        self.draw.text((text_left, banner_top), lines[0], fill=(255, 200, 200), font=header_font)
                    if lines[1]:
                        self.draw.text((text_left, banner_top + 28), lines[1], fill=(255, 255, 255), font=msg_font)
                else:
                    # Banner 2: word-wrapped message (3 lines)
                    line_height = 22
                    for i in range(min(len(lines), 3)):
                        y = banner_top + (i * line_height)
                        self.draw.text((text_left, y), lines[i], fill=(255, 255, 255), font=msg_font)
            else:
                # Show subtle hint so user knows they can interact
                hint_text = "• A: read  B: dismiss"
                self.draw.text((text_left, banner_top + 20), hint_text, fill=(120, 120, 140), font=hint_font)

        # Approval alternating banners
        elif self.state == STATE_AWAITING_APPROVAL and hasattr(self, '_approval_banners'):
            # Advance banner timer and swap
            self._approval_banner_timer += 1
            if self._approval_banner_timer >= self._approval_banner_interval:
                self._approval_banner_timer = 0
                self._approval_banner_index = (self._approval_banner_index + 1) % len(self._approval_banners)

            try:
                header_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 24
                )
                msg_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19
                )
            except OSError:
                header_font = ImageFont.load_default()
                msg_font = header_font

            lines = self._approval_banners[self._approval_banner_index]
            is_banner1 = (self._approval_banner_index == 0)

            if is_banner1:
                # Banner 1: header + action (2 lines)
                self.draw.text((text_left, banner_top), lines[0], fill=(255, 200, 200), font=header_font)
                if lines[1]:
                    self.draw.text((text_left, banner_top + 28), lines[1], fill=(255, 255, 255), font=msg_font)
            else:
                # Banner 2: description (3 lines)
                line_height = 22
                for i in range(min(len(lines), 3)):
                    y = banner_top + (i * line_height)
                    self.draw.text((text_left, y), lines[i], fill=(255, 255, 255), font=msg_font)

        # Status message overlay (non-approval, non-notification)
        elif self.status_timer > 0:
            try:
                status_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24
                )
            except OSError:
                status_font = ImageFont.load_default()
            # Word-wrap text within the banner area
            words = self.status_message.split(' ')
            lines = []
            current_line = ''
            for word in words:
                test_line = f'{current_line} {word}'.strip() if current_line else word
                bbox = status_font.getbbox(test_line)
                if bbox[2] - bbox[0] > max_text_width and current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = test_line
            if current_line:
                lines.append(current_line)
            y = banner_top
            for line in lines:
                if y + 24 > banner_bottom:
                    break
                self.draw.text((text_left, y), line, fill=(255, 255, 255), font=status_font)
                y += 24

    def show_status_summary(self):
        """Show detailed status summary"""
        self.status_message = f"{self.state.upper()} | Mute: {self.mute}"
        self.status_timer = 90  # 3 seconds at 30fps

    def trigger_heartbeat(self):
        """Trigger a heartbeat check"""
        self.status_message = "❤️ Heartbeat OK"
        self.status_timer = 60

    def toggle_sleep_mode(self):
        """Toggle sleep mode for screen (replaces cycle_state)"""
        self.screen_asleep = not self.screen_asleep
        
        if self.screen_asleep:
            # Entering sleep mode
            self.status_message = "💤 Sleep mode"
            self.status_timer = 60  # 2 seconds at 30fps
            self.led_timer = 0  # Turn off LED immediately
        else:
            # Waking up
            self.status_message = "☀️ Wake up"
            self.status_timer = 45  # 1.5 seconds at 30fps
            # Restore LED for current state
            self.led_timer = 10

    def cycle_state(self):
        """Cycle through states for demo"""
        states = [STATE_SLEEPING, STATE_PROCESSING, STATE_COMMUNICATING, STATE_ERROR]
        current_idx = states.index(self.state)
        self.state = states[(current_idx + 1) % len(states)]
        self.status_message = f"State: {self.state.upper()}"
        self.status_timer = 45

        # Reset face animation when state changes

        # LED on for 10 seconds on state change
        self.led_timer = 10
        self.face_index = 0
        self.face_timer = 0
        faces = FaceExpressions.get_faces_for_state(self.state)
        self.current_face = faces[0] if faces else "(◕‿‿◕)"

    def toggle_mute(self):
        """Toggle mute/quiet mode"""
        self.mute = not self.mute
        self.status_message = "Muted" if self.mute else "Unmuted"
        self.status_timer = 45

    # ── Thermal sensor callbacks ────────────────────────────────────

    def _on_thermal_presence_change(self, absent, present, ambient_temp=None):
        """Callback from thermal sensor when presence state changes.
        Runs in the thermal sensor's thread — must be thread-safe."""
        now = time.time()

        # Detect present→absent transition for leaving face
        if not present and self._prev_env_present:
            self._leaving_face_active = True
            self._leaving_face_timer = 0.5  # 500ms leaving face

        self._prev_env_present = present

        with self._environmental_lock:
            self._environmental_state["present"] = present
            self._environmental_state["last_change"] = now
            self._environmental_state["ambient_temp"] = ambient_temp
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
            self._brightness_ramp_duration = 0.7
        else:
            # Person disappeared — dim down immediately (no perceptual delay on leaving)
            self._proximity_face_pending = "(≖◡◡≖)"  # Dozing
            self._proximity_face_time = now
            self._brightness_target = 0.2
            self._brightness_ramp_start = now
            self._brightness_ramp_duration = 0.9  # Slower fade-out

        print(f"[snarling] Presence change: absent={absent}, present={present}")

    def _on_thermal_proximity_change(self, old_zone, new_zone, proximity, ambient_temp=None):
        """Called by ThermalSensor when proximity zone changes.
        Runs in the thermal sensor's thread — must be thread-safe."""
        now = time.time()
        with self._environmental_lock:
            self._environmental_state["proximity"] = proximity
            self._environmental_state["proximity_zone"] = new_zone
            self._environmental_state["last_change"] = now
            self._environmental_state["source"] = "thermal"
            self._environmental_state["ambient_temp"] = ambient_temp

        # Brightness targets based on proximity zone
        if new_zone == "present":
            # Close range — full brightness with slight overshoot for "lock-on"
            self._brightness_target = 1.0  # Full brightness on close proximity
            self._proximity_face_pending = "(◠‿◠)"  # I see you
            self._proximity_face_time = now + 0.20  # 200ms after detection
            self._brightness_ramp_start = now
            self._brightness_ramp_duration = 0.7
        elif new_zone == "approaching":
            # Mid range — partial brightness
            self._brightness_target = 0.3 + proximity * 0.7
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

    @staticmethod
    def _ease_out_cubic(t):
        """Ease-out cubic: fast start, slow settle. t in [0, 1]."""
        return 1 - (1 - t) ** 3

    # ── End thermal callbacks ───────────────────────────────────────

    def approve_request(self):
        """Handle approval button press (A button in approval state)"""
        if self.state == STATE_AWAITING_APPROVAL:
            print("[snarling] Request APPROVED by user")
            self.approval_counts["approved"] += 1
            print(f"[snarling] Running total — approved: {self.approval_counts['approved']}, rejected: {self.approval_counts['rejected']}")
            self.status_message = "✓ APPROVED"
            self.status_timer = 60
            # Forward approval response
            self.forward_approval_response(approved=True)
            # Return to sleeping state after approval
            self.state = STATE_SLEEPING
            self.led_timer = 0
            # If notifications were queued during approval, show them now
            if self._notify_stack:
                self._activate_next_notification()

    def reject_request(self):
        """Handle rejection button press (B button in approval state)"""
        if self.state == STATE_AWAITING_APPROVAL:
            print("[snarling] Request REJECTED by user")
            self.approval_counts["rejected"] += 1
            print(f"[snarling] Running total — approved: {self.approval_counts['approved']}, rejected: {self.approval_counts['rejected']}")
            self.status_message = "✗ REJECTED"
            self.status_timer = 60
            # Forward approval response
            self.forward_approval_response(approved=False)
            # Return to sleeping state after rejection
            self.state = STATE_SLEEPING
            self.led_timer = 0
            # If notifications were queued during approval, show them now
            if self._notify_stack:
                self._activate_next_notification()

    def forward_approval_response(self, approved):
        request_id = getattr(self, '_pending_approval_id', 'unknown')
        session_key = getattr(self, '_pending_session_key', None) or 'agent:main:main'
        print(f"[snarling] Forwarding approval for {request_id}: {'APPROVED' if approved else 'REJECTED'} (sessionKey={session_key})")
        try:
            import requests as req_lib
            # Call OpenClaw's approval-callback webhook
            gateway_token = "c1e2798a58fcf2414a4602f743a193838f6e4416eb5a61ed"
            webhook_url = "http://localhost:18789/approval-callback"
            response_data = {
                "request_id": request_id,
                "approved": approved,
                "secret": getattr(self, '_pending_approval_secret', None),
                "sessionKey": session_key,
                "flow_id": getattr(self, '_pending_flow_id', None)
            }
            response = req_lib.post(
                webhook_url,
                json=response_data,
                headers={"Authorization": f"Bearer {gateway_token}"},
                timeout=5
            )
            print(f"[snarling] Webhook status: {response.status_code}")
            if response.status_code == 200:
                print(f"[snarling] OpenClaw acknowledged: {response.json().get('message', 'OK')}")

            # Wake approach: After the webhook callback, use the OpenClaw WebSocket RPC
            # API to trigger a heartbeat. This is a separate process (snarling), so it
            # bypasses the "requests-in-flight" issue that internal plugin wake calls
            # suffer from. The WebSocket protocol requires a challenge-response handshake.
            try:
                import threading
                import json as json_mod
                
                def delayed_wake():
                    import time
                    time.sleep(2)  # Wait for webhook handler + session lane to fully unwind
                    try:
                        import websocket
                        ws = websocket.create_connection(
                            'ws://127.0.0.1:18789/ws',
                            timeout=10,
                            header=['Authorization: Bearer ' + gateway_token]
                        )
                        # Handle challenge-response handshake
                        challenge_raw = ws.recv()
                        challenge_data = json_mod.loads(challenge_raw)
                        if challenge_data.get('event') == 'connect.challenge':
                            nonce = challenge_data['payload']['nonce']
                            ws.send(json_mod.dumps({'type': 'response', 'payload': {'nonce': nonce}}))
                            print(f'[snarling] WebSocket challenge completed')
                        else:
                            print(f'[snarling] Unexpected first message: {str(challenge_raw)[:100]}')
                        
                        # Trigger immediate heartbeat via RPC
                        hb_msg = json_mod.dumps({
                            'type': 'rpc',
                            'method': 'system.runHeartbeatOnce',
                            'params': {
                                'sessionKey': session_key,
                                'reason': 'hook:approval',
                                'heartbeat': {'target': 'last'}
                            }
                        })
                        ws.send(hb_msg)
                        hb_result = ws.recv()
                        print(f'[snarling] Heartbeat trigger result: {str(hb_result)[:200]}')
                        ws.close()
                    except Exception as e:
                        print(f'[snarling] WebSocket wake failed: {e}')
                
                wake_thread = threading.Thread(target=delayed_wake, daemon=True)
                wake_thread.start()
            except Exception as wake_err:
                print(f'[snarling] Delayed wake setup failed: {wake_err}')
        except Exception as e:
            print(f"[snarling] Webhook call failed: {e}")

    def forward_notification_feedback(self, revealed, time_to_reveal_sec, dismissed, timed_out=False, time_in_queue_sec=0):
        """Send notification interaction feedback back to the gateway.
        Mirrors forward_approval_response exactly: same gateway token, same auth, same WebSocket wake.
        time_to_reveal_sec = display time only (from when notification appeared on screen)
        time_in_queue_sec = time spent queued behind other notifications"""
        notify_id = self._notify_id
        callback_url = self._notify_callback_url
        session_key = self._notify_session_key or 'agent:main:main'
        secret = self._notify_secret

        if not notify_id or not callback_url:
            print(f"[snarling] No callback for notification (id={notify_id}, url={callback_url}) — skipping feedback")
            return

        print(f"[snarling] Forwarding notification feedback for {notify_id}: revealed={revealed}, time_to_reveal={time_to_reveal_sec + time_in_queue_sec:.1f}s (display={time_to_reveal_sec:.1f}s + queue={time_in_queue_sec:.1f}s), dismissed={dismissed}, timed_out={timed_out} (sessionKey={session_key})")
        try:
            import requests as req_lib
            gateway_token = "c1e2798a58fcf2414a4602f743a193838f6e4416eb5a61ed"

            # Add presence data from environmental state
            try:
                with self._environmental_lock:
                    present_at_feedback = self._environmental_state["present"]
                    proximity_at_feedback = round(self._environmental_state["proximity"], 2)
            except Exception:
                present_at_feedback = None
                proximity_at_feedback = None

            # If thermal is unavailable, present should be None (not False)
            if not self._thermal_available:
                present_at_feedback = None
                proximity_at_feedback = None

            response_data = {
                "notification_id": notify_id,
                "revealed": revealed,
                "time_to_reveal_sec": time_to_reveal_sec + time_in_queue_sec,  # total time from send to reveal
                "dismissed": dismissed,
                "timed_out": timed_out,
                "secret": secret,
                "sessionKey": session_key,
                "present": present_at_feedback,
                "proximity": proximity_at_feedback,
            }
            response = req_lib.post(
                callback_url,
                json=response_data,
                headers={"Authorization": f"Bearer {gateway_token}"},
                timeout=5
            )
            print(f"[snarling] Notification callback status: {response.status_code}")
            if response.status_code == 200:
                print(f"[snarling] Gateway acknowledged: {response.json().get('message', 'OK')}")

            # Wake approach: After the callback, use the OpenClaw WebSocket RPC
            # API to trigger a heartbeat — same pattern as forward_approval_response.
            try:
                import threading
                import json as json_mod

                def delayed_wake():
                    import time
                    time.sleep(2)  # Wait for callback handler + session lane to fully unwind
                    try:
                        import websocket
                        ws = websocket.create_connection(
                            'ws://127.0.0.1:18789/ws',
                            timeout=10,
                            header=['Authorization: Bearer ' + gateway_token]
                        )
                        # Handle challenge-response handshake
                        challenge_raw = ws.recv()
                        challenge_data = json_mod.loads(challenge_raw)
                        if challenge_data.get('event') == 'connect.challenge':
                            nonce = challenge_data['payload']['nonce']
                            ws.send(json_mod.dumps({'type': 'response', 'payload': {'nonce': nonce}}))
                            print(f'[snarling] WebSocket challenge completed (notification feedback)')
                        else:
                            print(f'[snarling] Unexpected first message: {str(challenge_raw)[:100]}')

                        # Trigger immediate heartbeat via RPC
                        hb_msg = json_mod.dumps({
                            'type': 'rpc',
                            'method': 'system.runHeartbeatOnce',
                            'params': {
                                'sessionKey': session_key,
                                'reason': 'hook:notification',
                                'heartbeat': {'target': 'last'}
                            }
                        })
                        ws.send(hb_msg)
                        hb_result = ws.recv()
                        print(f'[snarling] Heartbeat trigger result (notification): {str(hb_result)[:200]}')
                        ws.close()
                    except Exception as e:
                        print(f'[snarling] WebSocket wake failed (notification): {e}')

                wake_thread = threading.Thread(target=delayed_wake, daemon=True)
                wake_thread.start()
            except Exception as wake_err:
                print(f'[snarling] Delayed wake setup failed (notification): {wake_err}')
        except Exception as e:
            print(f"[snarling] Notification callback failed: {e}")

    def _notify_sort_key(self, item):
        """Sort key for notification stack: high first, then normal, then low.
        Within same priority, higher _seq (newer) comes first (LIFO)."""
        rank = {'high': 0, 'normal': 1, 'low': 2}.get(item.get('priority', 'normal'), 1)
        return (rank, -item.get('_seq', 0))

    def _prepare_notify_banners(self, message, priority):
        """Build the alternating banners for a notification and set them on self."""
        try:
            banner_header_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 24
            )
            banner_msg_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19
            )
        except OSError:
            banner_header_font = ImageFont.load_default()
            banner_msg_font = banner_header_font

        def word_wrap(text, font, max_width):
            """Word-wrap text to fit within max_width pixels using the given font."""
            words = text.split()
            lines = []
            current = ""
            for word in words:
                test = f"{current} {word}".strip() if current else word
                bbox = font.getbbox(test)
                text_width = bbox[2] - bbox[0]
                if text_width <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    word_bbox = font.getbbox(word)
                    word_width = word_bbox[2] - word_bbox[0]
                    if word_width > max_width:
                        current = word[:len(word)//2] + ".."
                    else:
                        current = word
            if current:
                lines.append(current)
            return lines

        # Banner 1: Priority header + short preview
        priority_headers = {
            'high': "!! HIGH",
            'normal': "* MODERATE",
            'low': "~ LOW",
        }
        header = priority_headers.get(priority, "* NOTIFICATION")
        # Preview line: first ~25 chars of message, word-wrapped
        preview_text = message[:25]
        # If we cut in the middle of a word, truncate at last space
        if len(message) > 25 and ' ' in preview_text:
            preview_text = preview_text.rsplit(' ', 1)[0]
        # Word-wrap preview with header font
        preview_lines = word_wrap(preview_text, banner_header_font, max_width=280)
        preview_line = preview_lines[0] if preview_lines else ""
        banner1 = [header, preview_line]

        # Banner 2: First 3 lines of the full message
        msg_lines = word_wrap(message, banner_msg_font, max_width=280)
        banner2_lines = msg_lines[:3]
        # If there's more, truncate last line with "..."
        if len(msg_lines) > 3:
            banner2_lines = msg_lines[:3]
            banner2_lines[2] = banner2_lines[2][:30] + "..."
        while len(banner2_lines) < 3:
            banner2_lines.append("")
        banner2 = banner2_lines

        # Banner 3: Continuation — lines 4+ of the full message
        remaining_lines = msg_lines[3:] if len(msg_lines) > 3 else []
        if remaining_lines:
            banner3_lines = remaining_lines[:3]
            # If still more, truncate last line with "..."
            if len(remaining_lines) > 3:
                banner3_lines = remaining_lines[:3]
                banner3_lines[2] = banner3_lines[2][:30] + "..."
            while len(banner3_lines) < 3:
                banner3_lines.append("")
            banner3 = banner3_lines
        else:
            # No continuation needed — skip banner 3 (empty)
            banner3 = None

        self._notify_banners = [b for b in [banner1, banner2, banner3] if b is not None]
        self._notify_banner_index = 0
        self._notify_banner_timer = 0
        self._notify_banner_interval = 45  # ~1.5s at 30fps

    def set_notification(self, message, priority='normal', notification_id=None, callback_url=None, session_key=None, secret=None, duration=None):
        """Set state to notifying with message and priority.
        If a notification is already active, queue this one in the stack.
        The stack is priority-sorted (high > normal > low, LIFO within same priority).
        If an approval is pending, just queue — don't interrupt."""
        # Add to stack with a monotonically increasing sequence number
        self._notify_seq += 1
        item = {
            "message": message, "priority": priority, "_seq": self._notify_seq,
            "notification_id": notification_id, "callback_url": callback_url,
            "session_key": session_key, "secret": secret,
            "duration": duration if duration is not None else (300 if priority == 'low' else 0),
            "sent_time": time.time()  # epoch when notification arrived at snarling
        }
        print(f"[snarling] set_notification: priority={priority}, duration_in={duration}, item_duration={item['duration']}")
        self._notify_stack.append(item)
        # Stable sort: primary key = priority rank (high=0, normal=1, low=2),
        # secondary key = -_seq so newer items sort first within same priority
        self._notify_stack.sort(key=self._notify_sort_key)

        # Don't interrupt an active approval — notifications will show after it's resolved
        if self.state == STATE_AWAITING_APPROVAL:
            print(f"[snarling] Notification queued (approval pending): priority={priority}, stack_size={len(self._notify_stack)}")
            return

        # If already displaying a notification, check for priority bump
        if self._notify_active and self.state == STATE_NOTIFYING:
            priority_rank = {'high': 0, 'normal': 1, 'low': 2}
            current_rank = priority_rank.get(self._notify_priority, 1)
            new_rank = priority_rank.get(priority, 1)

            if not self._notify_text_revealed and new_rank < current_rank:
                # Bump current notification back to stack
                self._notify_seq += 1
                bumped = {
                    "message": self._notify_message, "priority": self._notify_priority, "_seq": self._notify_seq,
                    "notification_id": self._notify_id, "callback_url": self._notify_callback_url,
                    "session_key": self._notify_session_key, "secret": self._notify_secret,
                    "duration": self._notify_duration
                }
                self._notify_stack.append(bumped)
                self._notify_stack.sort(key=self._notify_sort_key)
                print(f"[snarling] Priority bump: '{priority}' replaces '{self._notify_priority}' on screen (text not revealed)")
                # _activate_next_notification preserves _notify_pre_state (only sets it if state != NOTIFYING)
                self._activate_next_notification()
                return
            else:
                # Just queue, don't interrupt
                print(f"[snarling] Notification queued: priority={priority}, stack_size={len(self._notify_stack)}, message='{message[:50]}'")
                return

        # No active notification — pop first from stack and display it
        self._activate_next_notification()

    def _activate_next_notification(self):
        """Pop the first item from _notify_stack and make it the currently-displaying notification."""
        if not self._notify_stack:
            return
        item = self._notify_stack.pop(0)  # remove from pending queue
        message = item['message']
        priority = item['priority']

        # Save previous state so we can return to it after all notifications
        if self.state != STATE_NOTIFYING:
            self._notify_pre_state = self.state

        self._notify_active = True
        self._notify_priority = priority
        self._notify_message = message
        self._notify_start_time = time.time()
        self._notify_text_revealed = False
        # Store feedback metadata from stack item
        self._notify_id = item.get('notification_id')
        self._notify_callback_url = item.get('callback_url')
        self._notify_session_key = item.get('session_key')
        self._notify_secret = item.get('secret')
        self._notify_duration = item.get('duration', 300) or 0  # 0 = no timeout (stays until dismissed)
        self._notify_sent_time = item.get('sent_time', 0)  # when notification arrived at snarling

        # Prepare banners
        self._prepare_notify_banners(message, priority)

        # Set state to notifying
        self.state = STATE_NOTIFYING
        # Reset face animation
        self.face_index = 0
        self.face_timer = 0
        notify_faces = FaceExpressions.get_notify_faces(priority)
        self.current_face = notify_faces[0] if notify_faces else "(•_•)"
        # LED on until dismissed
        self.led_timer = 216000  # effectively indefinite
        total = 1 + len(self._notify_stack)
        print(f"[snarling] Notification set: priority={priority}, message='{message[:50]}' ({1}/{total} in stack), notify_id={self._notify_id}")

    def _dismiss_notification(self):
        """Dismiss the current notification. If stack has items, show next; otherwise restore state."""
        if not self._notify_active:
            return

        # If there are pending notifications in the stack, show the next one
        if self._notify_stack:
            item = self._notify_stack.pop(0)
            message = item['message']
            priority = item['priority']
            # Update current notification attributes
            self._notify_priority = priority
            self._notify_message = message
            self._notify_start_time = time.time()
            self._notify_text_revealed = False
            # Store feedback metadata from stack item
            self._notify_id = item.get('notification_id')
            self._notify_callback_url = item.get('callback_url')
            self._notify_session_key = item.get('session_key')
            self._notify_secret = item.get('secret')
            self._notify_duration = item.get('duration', 300) or 0  # 0 = no timeout
            self._notify_sent_time = item.get('sent_time', 0)  # when notification arrived at snarling
            # Prepare banners for the new notification
            self._prepare_notify_banners(message, priority)
            # Reset face animation for new priority
            self.face_index = 0
            self.face_timer = 0
            notify_faces = FaceExpressions.get_notify_faces(priority)
            self.current_face = notify_faces[0] if notify_faces else "(•_•)"
            total = 1 + len(self._notify_stack)
            print(f"[snarling] Next notification from stack: priority={priority}, message='{message[:50]}' ({1}/{total} in stack)")
            return

        # Stack is empty — clear all notification state and restore pre-state
        prev_state = self._notify_pre_state
        self._notify_active = False
        self._notify_priority = 'normal'
        self._notify_message = ''
        self._notify_text_revealed = False
        self._notify_start_time = 0
        self._notify_pre_state = STATE_SLEEPING
        # Clean up notification feedback metadata
        self._notify_id = None
        self._notify_callback_url = None
        self._notify_session_key = None
        self._notify_secret = None
        self._notify_duration = 0
        # Clean up banner attributes
        self._notify_banners = []
        self._notify_banner_index = 0
        self._notify_banner_timer = 0
        self.state = prev_state
        # Update face immediately to match restored state
        faces = FaceExpressions.get_faces_for_state(prev_state)
        if faces:
            self.face_index = 0
            self.current_face = faces[0]
            self.face_timer = 0
        # LED off when sleeping, else brief on
        if prev_state == STATE_SLEEPING:
            self.led_timer = 0
        else:
            self.led_timer = 5
        print(f"[snarling] Notification dismissed, returning to {prev_state}")

    def set_awaiting_approval(self, request_id, message, flow_id=None, callback_secret=None, session_key=None):
        """Set state to awaiting approval with request details"""
        self.state = STATE_AWAITING_APPROVAL
        self._pending_approval_id = request_id
        self._pending_flow_id = flow_id
        self._pending_approval_secret = callback_secret
        self._pending_session_key = session_key
        # The message arrives as "action: description" from the approval server
        # Split on first ": " to separate action from description
        if ": " in message and not message.startswith(" "):
            parts = message.split(": ", 1)
            action_text = parts[0]
            desc_text = parts[1]
            print(f"[snarling] Split message - action: '{action_text}', desc: '{desc_text}'")
        else:
            action_text = "Approve?"
            desc_text = message
            print(f"[snarling] No ': ' found, full message as desc: '{message}'")
        # Word-wrap each line to fit within pixel width, breaking at word boundaries
        # Calculate usable width based on display dimensions and frame insets
        inner_inset = BORDER_MARGIN + INNER_FRAME_INSET + 1
        text_left = inner_inset + 10
        text_right_calc = WIDTH - inner_inset - 30
        usable_width = text_right_calc - text_left
        def word_wrap(text, font, max_width):
            """Word-wrap text to fit within max_width pixels using the given font."""
            words = text.split()
            lines = []
            current = ""
            for word in words:
                test = f"{current} {word}".strip() if current else word
                bbox = font.getbbox(test)
                text_width = bbox[2] - bbox[0]
                if text_width <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    # If single word is too wide, truncate with ellipsis
                    word_bbox = font.getbbox(word)
                    word_width = word_bbox[2] - word_bbox[0]
                    if word_width > max_width:
                        current = word[:len(word)//2] + ".."
                    else:
                        current = word
            if current:
                lines.append(current)
            return lines
        # Load fonts for pixel-accurate word wrapping
        try:
            wrap_header_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 24
            )
            wrap_msg_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 19
            )
        except OSError:
            wrap_header_font = ImageFont.load_default()
            wrap_msg_font = wrap_header_font
        # Banner 1: Approve header + action name
        # Banner 2: message word-wrapped across two lines
        header = "Approve? A=Yes B=No"
        action_lines = word_wrap(action_text, wrap_header_font, max_width=300)
        while len(action_lines) < 1:
            action_lines.append("")
        banner1 = [header, action_lines[0]]
        # Banner 2: first 3 lines of description word-wrapped
        desc_lines = word_wrap(desc_text, wrap_msg_font, max_width=usable_width)
        banner2_lines = desc_lines[:3]
        if len(desc_lines) > 3:
            banner2_lines = desc_lines[:3]
            banner2_lines[2] = banner2_lines[2][:30] + "..."
        while len(banner2_lines) < 3:
            banner2_lines.append("")
        banner2 = banner2_lines

        # Banner 3: continuation — lines 4+ of description
        remaining_lines = desc_lines[3:] if len(desc_lines) > 3 else []
        if remaining_lines:
            banner3_lines = remaining_lines[:3]
            if len(remaining_lines) > 3:
                banner3_lines = remaining_lines[:3]
                banner3_lines[2] = banner3_lines[2][:30] + "..."
            while len(banner3_lines) < 3:
                banner3_lines.append("")
            banner3 = banner3_lines
        else:
            banner3 = None

        print(f"[snarling] Banner1: {banner1}")
        print(f"[snarling] Banner2: {banner2}")
        if banner3:
            print(f"[snarling] Banner3: {banner3}")
        self._approval_banners = [b for b in [banner1, banner2, banner3] if b is not None]
        self._approval_banner_index = 0
        self._approval_banner_timer = 0
        self._approval_banner_interval = 45  # frames per banner (~1.5s at 30fps)
        self.status_timer = 216000  # 2 hours at 30fps
        self.led_timer = 216000  # Keep LED on for 2 hours
        print(f"[snarling] Awaiting approval for: {request_id}")

    def check_buttons(self):
        """Check for button presses"""
        buttons = {
            'A': self.display.BUTTON_A,
            'B': self.display.BUTTON_B,
            'X': self.display.BUTTON_X,
            'Y': self.display.BUTTON_Y
        }

        for name, button in buttons.items():
            pressed = self.display.read_button(button)

            if pressed and not self.button_pressed[name]:
                # Button just pressed - check approval state first
                if self.state == STATE_AWAITING_APPROVAL:
                    if name == 'A':
                        self.approve_request()
                    elif name == 'B':
                        self.reject_request()
                    # X and Y don't do anything in approval state
                elif self.state == STATE_NOTIFYING and self._notify_active:
                    # Notification interaction
                    if name == 'A':
                        if not self._notify_text_revealed:
                            # Reveal notification text
                            self._notify_text_revealed = True
                            elapsed = time.time() - self._notify_start_time
                            queue_time = self._notify_start_time - self._notify_sent_time if self._notify_sent_time > 0 else 0
                            self.forward_notification_feedback(revealed=True, time_to_reveal_sec=elapsed, dismissed=False, time_in_queue_sec=queue_time)
                            print(f"[snarling] Notification text revealed: {self._notify_message[:50]} (took {elapsed:.1f}s)")
                        else:
                            # Dismiss notification after text was revealed (feedback already sent on reveal)
                            self._dismiss_notification()
                    elif name == 'B':
                        # Dismiss without revealing (snooze/dismiss)
                        self.forward_notification_feedback(revealed=False, time_to_reveal_sec=0, dismissed=True, time_in_queue_sec=self._notify_start_time - self._notify_sent_time if self._notify_sent_time > 0 else 0)
                        self._dismiss_notification()
                else:
                    # Normal button handling
                    if name == 'B':
                        self.trigger_heartbeat()
                    elif name == 'X':
                        self.toggle_sleep_mode()
                    elif name == 'Y':
                        self.toggle_mute()

            self.button_pressed[name] = pressed

    def update(self, dt):
        """Update creature state"""
        # Update breathing phase (2 second cycle)
        self.breath_phase = (self.breath_phase + dt * 3) % (2 * math.pi)

        # Update LED timer
        if self.led_timer > 0:
            self.led_timer = max(0, self.led_timer - dt)

        # Update face animation
        self.update_face(dt)

        # Thermal sensor health check (every ~5 seconds / ~150 frames)
        if self._thermal_available:
            self._thermal_health_counter += 1
            if self._thermal_health_counter >= 150:
                self._thermal_health_counter = 0
                if self.thermal is not None and not self.thermal.is_running:
                    print("[snarling] Thermal sensor thread died — marking as unavailable")
                    self._thermal_available = False
                    with self._environmental_lock:
                        self._environmental_state["present"] = False
                        self._environmental_state["proximity"] = 0.0
                        self._environmental_state["proximity_zone"] = "absent"
                    self._brightness_target = 0.2

        # State is now set via direct /state API from the plugin (no polling)

        # Update LED
        self.update_led()

        # Check notification timeout (only low-priority notifications auto-dismiss)
        # High and normal priority stay until the user interacts
        # duration=0 means "use priority-based default" (low=300s, others=no timeout)
        effective_duration = self._notify_duration if self._notify_duration > 0 else (300 if self._notify_priority == 'low' else 0)
        if self._notify_active and self.state == STATE_NOTIFYING and self._notify_start_time > 0:
            if self._notify_priority == 'low' and effective_duration > 0:
                elapsed_notify = time.time() - self._notify_start_time
                if elapsed_notify >= effective_duration:
                    print(f"[snarling] Low-priority notification timed out after {effective_duration}s")
                    queue_time = self._notify_start_time - self._notify_sent_time if self._notify_sent_time > 0 else 0
                    self.forward_notification_feedback(revealed=False, time_to_reveal_sec=0, dismissed=False, timed_out=True, time_in_queue_sec=queue_time)
                    self._dismiss_notification()

        # Decrement status timer every frame (even when screen is asleep)
        if self.status_timer > 0:
            self.status_timer -= 1
            # Check for approval timeout
            if self.status_timer == 0 and self.state == STATE_AWAITING_APPROVAL:
                print("[snarling] Approval request timed out")
                self.status_message = "⌛ TIMEOUT"
                self.status_timer = 60  # Show timeout message for 2 seconds
                # Forward timeout as rejection
                self.forward_approval_response(approved=False)
                # Return to sleeping state
                self.state = STATE_SLEEPING
                self.led_timer = 0

    def draw_frame(self):
        """Render the frame using the new design system"""
        # Check if screen is asleep (but allow status messages to show)
        if self.screen_asleep:
            # Render black screen when asleep
            self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0))
            
            # Only show status messages even in sleep mode
            if self.status_timer > 0:
                inset = BORDER_MARGIN + INNER_FRAME_INSET + 1
                banner_top = HEIGHT - inset - BANNER_HEIGHT + 4
                banner_bottom = HEIGHT - inset - 4
                text_left = inset + 10
                text_right = WIDTH - inset - 30
                max_text_width = text_right - text_left
                # Show in the banner area
                self.draw.rectangle(
                    (inset + 1, banner_top - 4, WIDTH - inset - 1, banner_bottom),
                    fill=COLOR_BANNER_BG
                )
                try:
                    status_font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24
                    )
                except OSError:
                    status_font = ImageFont.load_default()
                words = self.status_message.split(' ')
                lines = []
                current_line = ''
                for word in words:
                    test_line = f'{current_line} {word}'.strip() if current_line else word
                    bbox = status_font.getbbox(test_line)
                    if bbox[2] - bbox[0] > max_text_width and current_line:
                        lines.append(current_line)
                        current_line = word
                    else:
                        current_line = test_line
                if current_line:
                    lines.append(current_line)
                y = banner_top
                for line in lines:
                    if y + 24 > banner_bottom:
                        break
                    self.draw.text((text_left, y), line, fill=(255, 255, 255), font=status_font)
                    y += 24
            return
        
        # Normal rendering when awake — new design system
        # 1. Background
        self.draw_background()
        # 2. Outer border (mood ring)
        self.draw_outer_border()
        # 3. Inner frame
        self.draw_inner_frame()
        # 4. Button indicators
        self.draw_button_indicators()
        # 5. Status boxes
        self.draw_status_boxes()
        # 6. Face
        self.draw_face()
        # 7. Separator + banner background (conditional)
        if self._is_banner_active():
            self.draw_separator()
            self.draw_banner_background()
        # 8. Banner text
        self.draw_status()

    def render(self):
        """Render to display (with rotation)"""
        rotated = self.img.rotate(180)
        self.display.buffer = rotated
        self.display.display()

    def cleanup(self):
        """Clean up and clear screen"""
        print("\nCleaning up...")

        # Stop thermal sensor
        if self.thermal is not None:
            try:
                self.thermal.stop()
                print("[snarling] Thermal sensor stopped")
            except Exception as e:
                print(f"[snarling] Error stopping thermal sensor: {e}")

        # Turn off LED
        self.display.set_led(0, 0, 0)

        # Clear screen
        self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0))
        self.render()

        # Give display time to update
        time.sleep(0.1)

        print("Goodbye!")

    def run(self):
        """Main loop"""
        print("🐛 snarling Creature Started!")
        print("Controls:")
        print("  A: Show status summary")
        print("  B: Trigger heartbeat check")
        print("  X: Toggle sleep mode")
        print("  Y: Toggle mute/quiet mode")
        print("  Ctrl+C: Exit")
        print()

        # Start thermal sensor (after display is initialized)
        if self._thermal_available and self.thermal is not None:
            try:
                self.thermal.start()
                if self.thermal.is_running:
                    print("[snarling] Thermal sensor started")
                else:
                    print("[snarling] Thermal sensor failed to start — sensor unavailable")
                    self._thermal_available = False
            except Exception as e:
                print(f"[snarling] Thermal sensor start failed: {e}")
                self._thermal_available = False

        target_fps = 30
        frame_time = 1.0 / target_fps

        try:
            while self.running:
                frame_start = time.time()

                # Check inputs
                self.check_buttons()

                # Update state
                self.update(frame_time)

                # Draw frame
                self.draw_frame()
                self.render()

                # Frame timing
                elapsed = time.time() - frame_start
                sleep_time = frame_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()


# Flask server for receiving approval alerts (runs in background thread)
try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("Warning: Flask not available, approval server integration disabled")

approval_app = Flask(__name__) if FLASK_AVAILABLE else None
creature_instance = None  # Will hold reference to snarlingCreature instance

if FLASK_AVAILABLE and approval_app:
    @approval_app.route('/approval/alert', methods=['POST'])
    def approval_alert():
        """Receive approval alert or notification from approval server"""
        global creature_instance
        data = request.json
        
        if not data:
            return jsonify({"error": "No JSON data"}), 400
        
        # Check if this is a notification (vs an approval request)
        if data.get('type') == 'notification':
            message = data.get('message', '')
            priority = data.get('priority', 'normal')
            # Validate secret (same auth as approvals)
            secret = data.get('secret')
            if not secret:
                return jsonify({"error": "Missing secret"}), 401
            # Extract new feedback fields
            notification_id = data.get('notification_id')
            callback_url = data.get('callback_url')
            session_key = data.get('sessionKey')
            duration = data.get('duration', 300)
            if creature_instance:
                creature_instance.set_notification(message, priority=priority, notification_id=notification_id, callback_url=callback_url, session_key=session_key, secret=secret, duration=duration)
                print(f"[snarling] Received notification: priority={priority}, notify_id={notification_id}")
                return jsonify({"status": "notification_displayed"})
            else:
                return jsonify({"error": "snarling not initialized"}), 503
        
        # Original approval flow
        request_id = data.get('request_id')
        message = data.get('message', 'Approval required')
        flow_id = data.get('flow_id')  # OpenClaw TaskFlow ID
        callback_secret = data.get('secret')  # Secret for webhook callback auth
        session_key = data.get('sessionKey')  # Session key for callback routing
        
        if creature_instance:
            creature_instance.set_awaiting_approval(request_id, message, flow_id, callback_secret=callback_secret, session_key=session_key)
            print(f"[snarling] Received approval alert: {request_id} (sessionKey={session_key})")
            return jsonify({"status": "alert_displayed"})
        else:
            return jsonify({"error": "snarling not initialized"}), 503
    
    @approval_app.route('/debug/notifications', methods=['GET'])
    def debug_notifications():
        """Debug endpoint to check current notification state"""
        global creature_instance
        if not creature_instance:
            return jsonify({"error": "snarling not initialized"}), 503

        return jsonify({
            "active": creature_instance._notify_active,
            "state": creature_instance.state,
            "current_priority": creature_instance._notify_priority,
            "current_message": creature_instance._notify_message[:80] if creature_instance._notify_message else "",
            "text_revealed": creature_instance._notify_text_revealed,
            "notification_id": creature_instance._notify_id,
            "callback_url": creature_instance._notify_callback_url,
            "duration": creature_instance._notify_duration,
            "start_time": creature_instance._notify_start_time,
            "sent_time": creature_instance._notify_sent_time,
            "stack_size": len(creature_instance._notify_stack),
            "stack": [
                {"priority": item["priority"], "message": item["message"][:50], "seq": item.get("_seq", 0),
                 "notification_id": item.get("notification_id"), "callback_url": item.get("callback_url")}
                for item in creature_instance._notify_stack
            ]
        })

    @approval_app.route('/health', methods=['GET'])
    def approval_health():
        """Health check for approval server"""
        return jsonify({"status": "healthy"})

    @approval_app.route('/counts', methods=['GET'])
    def get_counts():
        """Get approval resolution counts"""
        if creature_instance:
            return jsonify({"resolved": creature_instance.approval_counts})
        else:
            return jsonify({"error": "snarling not initialized"}), 503

    @approval_app.route('/state', methods=['POST'])
    def set_state():
        """Set creature state directly (called by OpenClaw plugin)"""
        global creature_instance
        data = request.json

        if not data:
            return jsonify({"error": "No JSON data"}), 400

        state = data.get('state', '').lower()
        valid_states = [STATE_SLEEPING, STATE_PROCESSING, STATE_COMMUNICATING, STATE_ERROR]

        if state not in valid_states:
            return jsonify({"error": f"Invalid state. Must be one of: {valid_states}"}), 400

        if creature_instance:
            # Mark time so the render loop knows a direct state was set recently
            creature_instance.direct_state_time = time.time()
            # Don't override awaiting_approval state unless explicitly setting it
            if creature_instance.state == STATE_AWAITING_APPROVAL and state != STATE_AWAITING_APPROVAL:
                return jsonify({"status": "ignored", "reason": "awaiting_approval"})
            # During notification, store incoming state as the pre-state so it applies after dismiss
            if creature_instance.state == STATE_NOTIFYING and creature_instance._notify_active and state != STATE_NOTIFYING:
                creature_instance._notify_pre_state = state
                print(f"[snarling] State update queued for after notification: {state}")
                return jsonify({"status": "queued", "reason": "notifying", "pending_state": state})

            if state != creature_instance.state:
                old_state = creature_instance.state
                creature_instance.state = state
                # Update face immediately on state change
                faces = FaceExpressions.get_faces_for_state(state, getattr(creature_instance, '_notify_priority', None))
                if faces:
                    creature_instance.face_index = 0
                    creature_instance.current_face = faces[0]
                    creature_instance.face_timer = 0
                # LED on for 10 seconds on state change, off when sleeping
                if state == STATE_SLEEPING:
                    creature_instance.led_timer = 0
                else:
                    creature_instance.led_timer = 10
                print(f"[snarling] State set via API: {old_state} -> {state}")

            return jsonify({"status": "ok", "state": state})
        else:
            return jsonify({"error": "snarling not initialized"}), 503

    @approval_app.route('/presence', methods=['GET'])
    def get_presence():
        """Get current presence/proximity state from thermal sensor"""
        if not creature_instance:
            return jsonify({"error": "snarling not initialized"}), 503

        with creature_instance._environmental_lock:
            env = dict(creature_instance._environmental_state)

        # Supplement with live thermal sensor values (updated every frame)
        ambient = env.get("ambient_temp")
        if creature_instance.thermal and creature_instance._thermal_available:
            sensor_info = creature_instance.thermal.get_presence_info()
            ambient = sensor_info.get("ambient_temp", ambient)

        return jsonify({
            "present": env["present"],
            "proximity": round(env["proximity"], 3),
            "proximity_zone": env["proximity_zone"],
            "source": env["source"],
            "last_change": env["last_change"],
            "ambient_temp": round(ambient, 1) if ambient is not None else None,
            "thermal_available": creature_instance._thermal_available,
        })

    @approval_app.route('/environment', methods=['POST'])
    def update_environment():
        """Update environmental state from external sources.
        Accepts: {present: bool, proximity: float (0-1), proximity_zone: str, ambient_temp: float}
        Thermal sensor data takes precedence — external data is ignored when thermal is active."""
        global creature_instance
        if not creature_instance:
            return jsonify({"error": "snarling not initialized"}), 503

        # Thermal sensor takes precedence — reject external data when thermal is active
        if creature_instance._thermal_available:
            return jsonify({
                "status": "ignored",
                "reason": "thermal_sensor_active",
                "message": "Thermal sensor data takes precedence. External data ignored."
            })

        data = request.json
        if not data:
            return jsonify({"error": "No JSON data"}), 400

        now = time.time()

        # Track presence transitions for leaving-face detection
        new_present = data.get('present', None)
        if new_present is not None:
            old_present = creature_instance._prev_env_present
            if not new_present and old_present:
                creature_instance._leaving_face_active = True
                creature_instance._leaving_face_timer = 0.5
            creature_instance._prev_env_present = new_present

        with creature_instance._environmental_lock:
            if new_present is not None:
                creature_instance._environmental_state["present"] = new_present
            if 'proximity' in data:
                creature_instance._environmental_state["proximity"] = max(0.0, min(1.0, float(data['proximity'])))
            if 'proximity_zone' in data:
                creature_instance._environmental_state["proximity_zone"] = data['proximity_zone']
            if 'ambient_temp' in data:
                creature_instance._environmental_state["ambient_temp"] = float(data['ambient_temp'])
            creature_instance._environmental_state["source"] = data.get('source', creature_instance._environmental_state.get('source', 'external'))
            creature_instance._environmental_state["last_change"] = now

        # Trigger face/brightness transitions based on external presence data
        env_present = creature_instance._environmental_state["present"]
        env_proximity = creature_instance._environmental_state["proximity"]
        env_zone = creature_instance._environmental_state["proximity_zone"]

        if env_present and env_zone == "present":
            creature_instance._brightness_target = 1.0
            creature_instance._proximity_face_pending = "(◠‿◠)"
            creature_instance._proximity_face_time = now + 0.20
            creature_instance._brightness_ramp_start = now
            creature_instance._brightness_ramp_duration = 0.7
        elif env_present:
            creature_instance._brightness_target = 0.3 + env_proximity * 0.7
            creature_instance._proximity_face_pending = "(⊙◡⊙)"
            creature_instance._proximity_face_time = now + 0.15
            creature_instance._brightness_ramp_start = now
            creature_instance._brightness_ramp_duration = 0.7
        else:
            creature_instance._brightness_target = 0.2
            creature_instance._proximity_face_pending = "(≖◡◡≖)"
            creature_instance._proximity_face_time = now
            creature_instance._brightness_ramp_start = now
            creature_instance._brightness_ramp_duration = 0.9

        print(f"[snarling] Environment update: present={creature_instance._environmental_state['present']}, "
              f"proximity={creature_instance._environmental_state['proximity']:.2f}, "
              f"zone={creature_instance._environmental_state['proximity_zone']}")

        return jsonify({"status": "ok", "environment": creature_instance._environmental_state})
    
    def run_approval_server():
        """Run Flask server in background thread"""
        if approval_app:
            print("[snarling] Starting approval alert server on port 5000")
            approval_app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    creature = snarlingCreature()
    
    # Start approval server in background thread if Flask is available
    if FLASK_AVAILABLE:
        import threading
        creature_instance = creature
        approval_thread = threading.Thread(target=run_approval_server, daemon=True)
        approval_thread.start()
    
    creature.run()

