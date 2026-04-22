
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
COLOR_BG = (20, 30, 40)
COLOR_TEXT = (255, 255, 255)
COLOR_SLEEP = (100, 150, 255)
COLOR_PROCESS = (255, 168, 148)  # Light melon
COLOR_COMM = (0, 255, 220)
COLOR_ERROR = (255, 80, 80)

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
    NOTIFY_HIGH = ['(☉_☉)', '(ಠ_ಠ)', '(⌐■_■)', '(⚠_⚠)']
    # Normal: informative, curious, aware
    NOTIFY_NORMAL = ['(•_•)', '(◡_◡)', '(⊙_⊙)', '(◉_◉)']
    # Low: gentle, slight perk, relaxed
    NOTIFY_LOW = ['(´・ω・)', '(・_・)', '(︶ω︶)', '(◠‿◠)']

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

        # Notification stack (priority-sorted pending queue)
        self._notify_stack = []  # list of {"message": str, "priority": str, "_seq": int}
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




        # Initialize display
        self.img = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
        self.draw = ImageDraw.Draw(self.img)
        self.display = DisplayHATMini(self.img)

        # Set initial LED
        self.update_led()

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
        """Update LED based on state and breathing animation (0-1 float values)"""
        # Turn off LED when screen is asleep
        if self.screen_asleep:
            self.display.set_led(0, 0, 0)
            return
        
        if self.led_timer > 0:
            if self.state == STATE_SLEEPING:
                # Blue breathing LED (r, g, b as 0-1 floats)
                brightness = 0.3 + 0.2 * math.sin(self.breath_phase)
                brightness *= 0.7  # Reduce by 30%
                self.display.set_led(0, brightness * 0.25, brightness * 0.5)
            elif self.state == STATE_PROCESSING:
                # Melon LED hum (slow pulse)
                pulse = 0.3 + 0.25 * math.sin(self.breath_phase * 1.5)
                pulse *= 0.7  # Reduce by 30%
                self.display.set_led(pulse * 0.99, pulse * 0.54, pulse * 0.45)
            elif self.state == STATE_COMMUNICATING:
                # Cyan pulsing
                pulse = 0.5 + 0.5 * math.sin(self.breath_phase * 2)
                pulse *= 0.7  # Reduce by 30%
                self.display.set_led(0, pulse, pulse)
            elif self.state == STATE_ERROR:
                # Red alert
                blink = 1.0 if int(self.breath_phase * 3) % 2 == 0 else 0.4
                blink *= 0.7  # Reduce by 30%
                self.display.set_led(blink, 0, 0)
            elif self.state == STATE_AWAITING_APPROVAL:
                # Red LED flash for approval alert
                blink = 1.0 if int(self.breath_phase * 4) % 2 == 0 else 0.2
                blink *= 0.7  # Reduce by 30%
                self.display.set_led(blink, 0, 0)
            elif self.state == STATE_NOTIFYING:
                if self._notify_showing_notify_face:
                    # Priority-colored LED flash when showing notification face
                    led_color = NOTIFY_LED_COLORS.get(self._notify_priority, NOTIFY_LED_COLORS['normal'])
                    # Flash rate: high=fast, normal=medium, low=slow
                    flash_rates = {'high': 6, 'normal': 3, 'low': 1.5}
                    rate = flash_rates.get(self._notify_priority, 3)
                    blink = 1.0 if int(self.breath_phase * rate) % 2 == 0 else 0.3
                    blink *= 0.7
                    self.display.set_led(
                        led_color[0] * blink,
                        led_color[1] * blink,
                        led_color[2] * blink
                    )
                else:
                    # LED off when showing pre-state (normal) face
                    self.display.set_led(0, 0, 0)
        else:
            # LED off
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
        if self.face_timer > 2.0:  # Change face every 2 seconds for more variety
            if self.state == STATE_NOTIFYING and self._notify_active:
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

    def draw_face(self):
        """Draw the face expression in the center of the screen using DejaVuSansMono like pwnagotchi"""
        face = self.get_current_face()
        color = self.get_color()

        # Cache font lookup on first call - use DejaVuSansMono like pwnagotchi
        if not hasattr(self, '_cached_font'):
            self._cached_font_size = 48
            
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
        x = (WIDTH - text_img.width) // 2 + self.animation_offset_x
        y = (HEIGHT - text_img.height) // 2 + self.animation_offset_y - 10

        # Paste with alpha blending
        if text_img.mode == 'RGBA':
            mask = text_img.split()[3]  # Alpha channel
            self.img.paste(text_img, (x, y), mask)
        else:
            self.img.paste(text_img, (x, y))

    def draw_status(self):
        """Draw status bar at bottom"""
        # Mute indicator
        if self.mute:
            self.draw.text((10, HEIGHT - 25), "🔇", fill=(150, 150, 150))

        # State indicator
        state_text = f"State: {self.state.upper()}"
        self.draw.text((WIDTH - 120, HEIGHT - 25), state_text, fill=(200, 200, 200))

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

                # Dark background tinted by priority
                bg_colors = {
                    'high': (80, 30, 20),
                    'normal': (60, 50, 20),
                    'low': (50, 50, 30),
                }
                bg = bg_colors.get(self._notify_priority, bg_colors['normal'])

                if is_banner1:
                    # Banner 1: 60px (2 lines), anchored above status bar
                    banner_height = 60
                    overlay_top = HEIGHT - 30 - banner_height
                    overlay_bottom = HEIGHT - 30
                    self.draw.rectangle((0, overlay_top, WIDTH, overlay_bottom), fill=bg)
                    # Header line in brighter color, detail in white
                    # Add stack count indicator (e.g., "1/3") when text is revealed
                    total_notifications = 1 + len(self._notify_stack)
                    if total_notifications > 1:
                        count_indicator = f" ({1}/{total_notifications})"
                        self.draw.text((10, overlay_top + 4), lines[0] + count_indicator, fill=(255, 200, 200), font=header_font)
                    else:
                        self.draw.text((10, overlay_top + 4), lines[0], fill=(255, 200, 200), font=header_font)
                    if lines[1]:
                        self.draw.text((10, overlay_top + 32), lines[1], fill=(255, 255, 255), font=msg_font)
                else:
                    # Banner 2: 80px (3 lines), same top as banner 1, extends down
                    banner_height = 80
                    overlay_top = HEIGHT - 30 - 60  # Same top as banner 1
                    overlay_bottom = overlay_top + banner_height  # = HEIGHT - 10
                    self.draw.rectangle((0, overlay_top, WIDTH, overlay_bottom), fill=bg)
                    # All white, word-wrapped message
                    line_height = 22
                    for i in range(min(len(lines), 3)):
                        y = overlay_top + 4 + (i * line_height)
                        self.draw.text((10, y), lines[i], fill=(255, 255, 255), font=msg_font)
            else:
                # Show subtle hint at bottom so user knows they can interact
                hint_text = "• A: read  B: dismiss"
                self.draw.text((10, HEIGHT - 55), hint_text, fill=(120, 120, 120), font=hint_font)

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

            # Banner background - height adapts to content
            # Banner 1: 60px (2 lines), anchored above status bar
            # Banner 2: 80px (3 lines), extends down over status bar area
            lines = self._approval_banners[self._approval_banner_index]
            is_banner1 = (self._approval_banner_index == 0)
            if is_banner1:
                banner_height = 60
                overlay_top = HEIGHT - 30 - banner_height
                overlay_bottom = HEIGHT - 30
            else:
                banner_height = 80
                # Banner 2: same top as banner 1, but extends down into status bar area
                overlay_top = HEIGHT - 30 - 60  # Same top as banner 1
                overlay_bottom = overlay_top + banner_height  # = HEIGHT - 10
            self.draw.rectangle((0, overlay_top, WIDTH, overlay_bottom), fill=(60, 20, 20))

            if is_banner1:
                # Banner 1: red top header + white bottom detail
                self.draw.text((10, overlay_top + 4), lines[0], fill=(255, 200, 200), font=header_font)
                if lines[1]:
                    self.draw.text((10, overlay_top + 32), lines[1], fill=(255, 255, 255), font=msg_font)
            else:
                # Banner 2: up to 3 lines, same style (no red/white split)
                line_height = 22
                for i in range(min(len(lines), 3)):
                    y = overlay_top + 4 + (i * line_height)
                    self.draw.text((10, y), lines[i], fill=(255, 255, 255), font=msg_font)

        # Status message overlay (non-approval)
        elif self.status_timer > 0:
            # Semi-transparent background (2x taller, grows upward from original position)
            overlay_bottom = HEIGHT - 30
            overlay_top = overlay_bottom - 60
            overlay_left = 5
            overlay_right = WIDTH - 20
            self.draw.rectangle((0, overlay_top, overlay_right, overlay_bottom), fill=(40, 50, 60))
            # Status text (regular weight, 24pt)
            try:
                status_font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24
                )
            except OSError:
                status_font = ImageFont.load_default()
            # Word-wrap text within the box
            max_width = overlay_right - overlay_left - 5
            words = self.status_message.split(' ')
            lines = []
            current_line = ''
            for word in words:
                test_line = f'{current_line} {word}'.strip() if current_line else word
                bbox = status_font.getbbox(test_line)
                if bbox[2] - bbox[0] > max_width and current_line:
                    lines.append(current_line)
                    current_line = word
                else:
                    current_line = test_line
            if current_line:
                lines.append(current_line)
            y = overlay_top + 5
            for line in lines:
                if y + 24 > overlay_bottom:
                    break
                self.draw.text((overlay_left, y), line, fill=(255, 255, 255), font=status_font)
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

    def forward_approval_response(self, approved):
        """Forward approval response to the OpenClaw webhook"""
        request_id = getattr(self, '_pending_approval_id', 'unknown')
        session_key = getattr(self, '_pending_session_key', None) or 'agent:main:main'
        print(f"[snarling] Forwarding approval for {request_id}: {'APPROVED' if approved else 'REJECTED'} (sessionKey={session_key})")
        try:
            import requests as req_lib
            # Call OpenClaw's approval-callback webhook
            gateway_token = "c1e2798a58fcf2414a4602f743a193838f6e4416eb5a61ed"
            webhook_url = f"http://localhost:18789/approval-callback?sessionKey={session_key}"
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

    def _notify_sort_key(self, item):
        """Sort key for notification stack: high first, then normal, then low.
        Within same priority, higher _seq (newer) comes first (LIFO)."""
        rank = {'high': 0, 'normal': 1, 'low': 2}.get(item.get('priority', 'normal'), 1)
        return (rank, -item.get('_seq', 0))

    def _prepare_notify_banners(self, message, priority):
        """Build the two alternating banners for a notification and set them on self."""
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
        preview_lines = word_wrap(preview_text, banner_header_font, max_width=300)
        preview_line = preview_lines[0] if preview_lines else ""
        banner1 = [header, preview_line]

        # Banner 2: Full message word-wrapped to max 3 lines
        msg_lines = word_wrap(message, banner_msg_font, max_width=310)
        # Cap at 3 lines, truncating last line with "..." if needed
        if len(msg_lines) > 3:
            msg_lines = msg_lines[:3]
            msg_lines[2] = msg_lines[2][:30] + "..."
        while len(msg_lines) < 3:
            msg_lines.append("")
        banner2 = msg_lines

        self._notify_banners = [banner1, banner2]
        self._notify_banner_index = 0
        self._notify_banner_timer = 0
        self._notify_banner_interval = 45  # ~1.5s at 30fps

    def set_notification(self, message, priority='normal'):
        """Set state to notifying with message and priority.
        If a notification is already active, queue this one in the stack.
        The stack is priority-sorted (high > normal > low, LIFO within same priority)."""
        # Add to stack with a monotonically increasing sequence number
        self._notify_seq += 1
        item = {"message": message, "priority": priority, "_seq": self._notify_seq}
        self._notify_stack.append(item)
        # Stable sort: primary key = priority rank (high=0, normal=1, low=2),
        # secondary key = -_seq so newer items sort first within same priority
        self._notify_stack.sort(key=self._notify_sort_key)

        # If already displaying a notification, check for priority bump
        if self._notify_active and self.state == STATE_NOTIFYING:
            priority_rank = {'high': 0, 'normal': 1, 'low': 2}
            current_rank = priority_rank.get(self._notify_priority, 1)
            new_rank = priority_rank.get(priority, 1)

            if not self._notify_text_revealed and new_rank < current_rank:
                # Bump current notification back to stack
                self._notify_seq += 1
                bumped = {"message": self._notify_message, "priority": self._notify_priority, "_seq": self._notify_seq}
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
        print(f"[snarling] Notification set: priority={priority}, message='{message[:50]}' ({1}/{total} in stack)")

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
        # Banner text starts at x=10, display is 320px wide → usable = 310px
        usable_width = 310
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
        # Banner 2: description word-wrapped across two lines
        desc_lines = word_wrap(desc_text, wrap_msg_font, max_width=usable_width)
        while len(desc_lines) < 3:
            desc_lines.append("")
        banner2 = desc_lines[:3]
        print(f"[snarling] Banner1: {banner1}")
        print(f"[snarling] Banner2: {banner2}")
        self._approval_banners = [banner1, banner2]
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
                            print(f"[snarling] Notification text revealed: {self._notify_message[:50]}")
                        else:
                            # Dismiss notification early
                            self._dismiss_notification()
                    elif name == 'B':
                        # Dismiss without revealing (snooze/dismiss)
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

        # State is now set via direct /state API from the plugin (no polling)

        # Update LED
        self.update_led()

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
        """Render the frame"""
        # Check if screen is asleep (but allow status messages to show)
        if self.screen_asleep:
            # Render black screen when asleep
            self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0))
            
            # Only show status messages even in sleep mode
            if self.status_timer > 0:
                # Semi-transparent background (2x taller, grows upward)
                overlay_bottom = HEIGHT - 30
                overlay_top = overlay_bottom - 60
                overlay_left = 5
                overlay_right = WIDTH - 20
                self.draw.rectangle((0, overlay_top, overlay_right, overlay_bottom), fill=(40, 50, 60))
                try:
                    status_font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24
                    )
                except OSError:
                    status_font = ImageFont.load_default()
                # Word-wrap text within the box
                max_width = overlay_right - overlay_left - 5
                words = self.status_message.split(' ')
                lines = []
                current_line = ''
                for word in words:
                    test_line = f'{current_line} {word}'.strip() if current_line else word
                    bbox = status_font.getbbox(test_line)
                    if bbox[2] - bbox[0] > max_width and current_line:
                        lines.append(current_line)
                        current_line = word
                    else:
                        current_line = test_line
                if current_line:
                    lines.append(current_line)
                y = overlay_top + 5
                for line in lines:
                    if y + 24 > overlay_bottom:
                        break
                    self.draw.text((overlay_left, y), line, fill=(255, 255, 255), font=status_font)
                    y += 24
            return
        
        # Normal rendering when awake
        # Clear background
        self.draw.rectangle((0, 0, WIDTH, HEIGHT), fill=COLOR_BG)

        # Draw elements
        self.draw_face()
        self.draw_status()

    def render(self):
        """Render to display (with rotation)"""
        rotated = self.img.rotate(180)
        self.display.buffer = rotated
        self.display.display()

    def cleanup(self):
        """Clean up and clear screen"""
        print("\nCleaning up...")



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
            if creature_instance:
                creature_instance.set_notification(message, priority=priority)
                print(f"[snarling] Received notification: priority={priority}")
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
            "stack_size": len(creature_instance._notify_stack),
            "stack": [
                {"priority": item["priority"], "message": item["message"][:50], "seq": item.get("_seq", 0)}
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

