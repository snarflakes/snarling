#!/usr/bin/env python3
"""Thermal camera MJPEG/RTMP streamer for Snarling.

Provides:
  - /thermal/stream  — MJPEG live stream (browser-compatible)
  - /thermal/frame   — Single JPEG snapshot (with overlay)
  - /thermal/raw     — Single JPEG snapshot (no overlay)
  - /thermal/        — HTML page with embedded stream

Can also push to RTMP (YouTube/Twitch) via FFmpeg subprocess.

Usage from snarling.py:
    from thermal_stream import ThermalStreamer
    streamer = ThermalStreamer(thermal_sensor, snarling_instance, secret=***)
    streamer.start()

The streamer reads thermal frames from ThermalSensor.latest_frame() and
presence state from snarling._environmental_state — no agent data, just
raw sensor perception.

Environment variables:
  THERMAL_STREAM_SECRET  — auth secret (defaults to APPROVAL_SECRET from snarling)
  THERMAL_RTMP_URL       — RTMP push URL (e.g. rtmp://a.rtmp.youtube.com/live2)
  THERMAL_RTMP_KEY       — RTMP stream key
  THERMAL_STREAM_PORT    — MJPEG server port (default 5001)
  THERMAL_STREAM_FPS     — Target FPS for MJPEG stream (default 2)
  THERMAL_STREAM_WIDTH   — Output frame width (default 240)
  THERMAL_STREAM_HEIGHT  — Output frame height (default 240)
"""

import io
import os
import subprocess
import threading
import time
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("thermal_stream")

# --- Thermal color mapping (from thermal_view.py) ---

def _temp_to_color(temp, t_min, t_max):
    """Map a temperature to an RGB color using a perceptual gradient."""
    if t_max <= t_min:
        t_max = t_min + 1.0
    t = max(0.0, min(1.0, (temp - t_min) / (t_max - t_min)))
    if t < 0.25:
        f = t / 0.25
        return (int(20 + 80 * f), 0, int(180 + 40 * f))
    elif t < 0.5:
        f = (t - 0.25) / 0.25
        return (int(100 - 80 * f), int(180 * f), int(220 - 120 * f))
    elif t < 0.75:
        f = (t - 0.5) / 0.25
        return (int(20 + 200 * f), int(180 + 75 * f), int(100 - 80 * f))
    else:
        f = (t - 0.75) / 0.25
        return (int(220 + 35 * f), int(255 - 80 * f), int(20 + 180 * f))


def _flood_fill(mask, rows, cols, start_r, start_c, visited):
    """Find connected component of warm pixels."""
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


def render_thermal_frame(rotated, rows, cols, width=240, height=240,
                         presence_state=None):
    """Render thermal frame data to a PIL Image with sensor overlay.

    Args:
        rotated: flat list of temperatures (rows * cols)
        rows: number of rows (32 after rotation)
        cols: number of cols (24 after rotation)
        width: output image width
        height: output image height
        presence_state: dict with keys from snarling._environmental_state
            - present (bool)
            - proximity_zone (str): absent/approaching/settled/departing
            - proximity (float): 0.0-1.0
            - ambient_temp (float)

    Returns:
        PIL Image object
    """
    if not rotated or len(rotated) != rows * cols:
        return None

    presence_state = presence_state or {}

    # Compute ambient and threshold
    EDGE_MARGIN = 2
    interior_temps = []
    for r in range(EDGE_MARGIN, rows - EDGE_MARGIN):
        row_offset = r * cols
        for c in range(EDGE_MARGIN, cols - EDGE_MARGIN):
            interior_temps.append(rotated[row_offset + c])
    interior_temps.sort()
    ambient = interior_temps[len(interior_temps) // 2]

    if ambient < 25:
        threshold = ambient + 3.0
    elif ambient < 30:
        threshold = ambient + 2.0
    else:
        threshold = ambient + 1.5

    # Color range
    all_temps = list(rotated)
    t_min = min(all_temps)
    t_max = max(all_temps)
    t_range = max(t_max - t_min, 2.0)
    display_min = t_min - t_range * 0.05
    display_max = t_max + t_range * 0.05

    # Build mask for warm pixels
    mask = [[False] * cols for _ in range(rows)]
    for r in range(EDGE_MARGIN, rows - EDGE_MARGIN):
        row_offset = r * cols
        for c in range(EDGE_MARGIN, cols - EDGE_MARGIN):
            if rotated[row_offset + c] > threshold:
                mask[r][c] = True

    # Find blobs for outlines
    visited = [[False] * cols for _ in range(rows)]
    blobs = []
    for r in range(rows):
        for c in range(cols):
            if mask[r][c] and not visited[r][c]:
                blob = _flood_fill(mask, rows, cols, r, c, visited)
                if len(blob) >= 15:
                    min_br = min(p[0] for p in blob)
                    max_br = max(p[0] for p in blob)
                    min_bc = min(p[1] for p in blob)
                    max_bc = max(p[1] for p in blob)
                    blobs.append((min_br, min_bc, max_br, max_bc))

    # Create image
    img = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Cell scale
    CELL_SCALE = min(width / cols, height / rows)
    hm_draw_w = int(cols * CELL_SCALE)
    hm_draw_h = int(rows * CELL_SCALE)
    hm_offset_x = (width - hm_draw_w) // 2

    # Render heat map
    for r in range(rows):
        for c in range(cols):
            temp = rotated[r * cols + c]
            color = _temp_to_color(temp, display_min, display_max)
            x0 = int(c * CELL_SCALE) + hm_offset_x
            y0 = int((rows - 1 - r) * CELL_SCALE)
            x1 = int((c + 1) * CELL_SCALE) + hm_offset_x
            y1 = int((rows - r) * CELL_SCALE)
            draw.rectangle((x0, y0, x1, y1), fill=color)

    # Warm pixel outlines
    for r in range(rows):
        for c in range(cols):
            if mask[r][c]:
                x0 = int(c * CELL_SCALE) + hm_offset_x
                y0 = int((rows - 1 - r) * CELL_SCALE)
                x1 = int((c + 1) * CELL_SCALE) + hm_offset_x
                y1 = int((rows - r) * CELL_SCALE)
                draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)

    # Blob rectangles
    for min_br, min_bc, max_br, max_bc in blobs:
        bx0 = int(min_bc * CELL_SCALE) + hm_offset_x - 1
        by0 = int((rows - 1 - max_br) * CELL_SCALE) - 1
        bx1 = int((max_bc + 1) * CELL_SCALE) + hm_offset_x + 1
        by1 = int((rows - min_br) * CELL_SCALE) + 1
        draw.rectangle((bx0, by0, bx1, by1), outline=(255, 80, 80), width=2)

    # --- Overlay text ---
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 18)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
        font_sm = font

    # Top left: ambient temp
    ambient_val = presence_state.get('ambient_temp', ambient)
    draw.text((5, 5), f"{ambient_val:.1f}°C", fill=(255, 255, 255), font=font)

    # Top left below: threshold
    draw.text((5, 25), f"Thr:{threshold:.1f}°C", fill=(200, 200, 200), font=font_sm)

    # Top right: presence state
    zone = presence_state.get('proximity_zone', 'absent').upper()
    present = presence_state.get('present', False)
    if present:
        state_text = zone if zone in ('SETTLED', 'APPROACHING') else 'PRESENT'
        state_color = (100, 255, 100)  # green
    else:
        state_text = 'ABSENT'
        state_color = (150, 150, 150)  # gray

    # Measure text width for right-alignment
    bbox = draw.textbbox((0, 0), state_text, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text((width - text_w - 5, 5), state_text, fill=state_color, font=font)

    # Bottom left: timestamp
    from datetime import datetime, timezone, timedelta
    pdt = timezone(timedelta(hours=-7))
    now_str = datetime.now(pdt).strftime("%b %d %H:%M PDT")
    draw.text((5, height - 20), now_str, fill=(180, 180, 180), font=font_sm)

    # Bottom right: watermark
    watermark = "snarling.ai"
    bbox_wm = draw.textbbox((0, 0), watermark, font=font_sm)
    wm_w = bbox_wm[2] - bbox_wm[0]
    draw.text((width - wm_w - 5, height - 20), watermark, fill=(120, 120, 120), font=font_sm)

    return img


def frame_to_jpeg(rotated, rows, cols, width=240, height=240, quality=85,
                  presence_state=None):
    """Render thermal frame and return as JPEG bytes."""
    img = render_thermal_frame(rotated, rows, cols, width, height,
                                presence_state=presence_state)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    return buf.getvalue()


class ThermalStreamer:
    """Manages thermal camera MJPEG stream and optional RTMP push."""

    def __init__(self, thermal_sensor, snarling=None, secret=None,
                 port=5001, fps=2, width=240, height=240):
        self.thermal = thermal_sensor
        self.snarling = snarling  # For presence state
        self.secret = secret or os.environ.get('THERMAL_STREAM_SECRET', '')
        self.port = int(os.environ.get('THERMAL_STREAM_PORT', port))
        self.fps = int(os.environ.get('THERMAL_STREAM_FPS', fps))
        self.width = int(os.environ.get('THERMAL_STREAM_WIDTH', width))
        self.height = int(os.environ.get('THERMAL_STREAM_HEIGHT', height))

        # RTMP config
        self.rtmp_url = os.environ.get('THERMAL_RTMP_URL', '')
        self.rtmp_key = os.environ.get('THERMAL_RTMP_KEY', '')

        self._server = None
        self._server_thread = None
        self._rtmp_process = None
        self._running = False
        self._latest_jpeg = None
        self._jpeg_lock = threading.Lock()
        self._frame_thread = None

    def start(self):
        """Start MJPEG server and optional RTMP push."""
        if self._running:
            return
        self._running = True

        # Start frame capture thread
        self._frame_thread = threading.Thread(target=self._frame_loop, daemon=True)
        self._frame_thread.start()

        # Start MJPEG HTTP server
        handler = _make_handler(self)
        self._server = HTTPServer(('0.0.0.0', self.port), handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        logger.info(f"[thermal_stream] MJPEG server started on port {self.port}")

        # Start RTMP push if configured
        if self.rtmp_url and self.rtmp_key:
            self._start_rtmp()

    def stop(self):
        """Stop everything."""
        self._running = False
        if self._server:
            self._server.shutdown()
        self._stop_rtmp()
        logger.info("[thermal_stream] Stopped")

    def _get_presence_state(self):
        """Read current presence state from snarling."""
        if self.snarling and hasattr(self.snarling, '_environmental_state'):
            return dict(self.snarling._environmental_state)
        return {}

    def _frame_loop(self):
        """Background thread: capture frames at target FPS."""
        interval = 1.0 / self.fps
        while self._running:
            try:
                frame_data = self.thermal.latest_frame()
                if frame_data is not None:
                    rotated, rows, cols = frame_data
                    presence_state = self._get_presence_state()
                    jpeg = frame_to_jpeg(rotated, rows, cols, self.width, self.height,
                                         presence_state=presence_state)
                    if jpeg:
                        with self._jpeg_lock:
                            self._latest_jpeg = jpeg
            except Exception as e:
                logger.warning(f"[thermal_stream] Frame capture error: {e}")
            time.sleep(interval)

    def get_jpeg(self):
        """Return latest JPEG frame bytes, or None."""
        with self._jpeg_lock:
            return self._latest_jpeg

    def _start_rtmp(self):
        """Start FFmpeg subprocess for RTMP push."""
        if not self.rtmp_url or not self.rtmp_key:
            return

        cmd = [
            'ffmpeg',
            '-re',                          # Real-time pacing
            '-f', 'image2pipe',             # Input: JPEG frames from pipe
            '-vcodec', 'mjpeg',
            '-framerate', str(self.fps),
            '-i', '-',                       # Read from stdin
            '-c:v', 'libx264',
            '-preset', 'ultrafast',          # Pi-friendly encoding
            '-tune', 'zerolatency',
            '-b:v', '500k',                  # Low bitrate (thermal is low-detail)
            '-maxrate', '500k',
            '-bufsize', '1000k',
            '-pix_fmt', 'yuv420p',
            '-s', f'{self.width}x{self.height}',
            '-r', str(self.fps),
            '-f', 'flv',
            f'{self.rtmp_url}/{self.rtmp_key}'
        ]

        logger.info(f"[thermal_stream] Starting RTMP push to {self.rtmp_url}")
        self._rtmp_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

        # Start RTMP writer thread
        self._rtmp_thread = threading.Thread(target=self._rtmp_write_loop, daemon=True)
        self._rtmp_thread.start()

    def _rtmp_write_loop(self):
        """Write JPEG frames to FFmpeg stdin for RTMP push."""
        interval = 1.0 / self.fps
        while self._running and self._rtmp_process and self._rtmp_process.poll() is None:
            jpeg = self.get_jpeg()
            if jpeg:
                try:
                    self._rtmp_process.stdin.write(jpeg)
                except (BrokenPipeError, OSError):
                    logger.warning("[thermal_stream] RTMP pipe closed")
                    break
            time.sleep(interval)

    def _stop_rtmp(self):
        """Stop RTMP push."""
        if self._rtmp_process and self._rtmp_process.poll() is None:
            self._rtmp_process.stdin.close()
            self._rtmp_process.terminate()
            try:
                self._rtmp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._rtmp_process.kill()
        self._rtmp_process = None


def _make_handler(streamer):
    """Create an HTTP request handler class with access to the streamer."""

    class ThermalStreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Auth check
            if streamer.secret:
                auth = self.headers.get('Authorization', '')
                if auth != f'Bearer {streamer.secret}':
                    parsed = urlparse(self.path)
                    params = parse_qs(parsed.query)
                    if params.get('secret', [''])[0] != streamer.secret:
                        self.send_response(401)
                        self.send_header('Content-Type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(b'Unauthorized')
                        return

            path = urlparse(self.path).path.rstrip('/')

            if path == '/thermal/stream':
                self._handle_mjpeg_stream()
            elif path == '/thermal/frame':
                self._handle_snapshot(overlay=True)
            elif path == '/thermal/overlay':
                self._handle_snapshot(overlay=True)
            elif path == '/thermal/raw':
                self._handle_snapshot(overlay=False)
            elif path == '/thermal':
                self._handle_index()
            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Not found')

        def _handle_mjpeg_stream(self):
            """Serve MJPEG stream."""
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()

            interval = 1.0 / streamer.fps
            while streamer._running:
                jpeg = streamer.get_jpeg()
                if jpeg:
                    try:
                        self.wfile.write(b'--frame\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', str(len(jpeg)))
                        self.end_headers()
                        self.wfile.write(jpeg)
                        self.wfile.write(b'\r\n')
                    except (BrokenPipeError, ConnectionResetError):
                        break
                time.sleep(interval)

        def _handle_snapshot(self, overlay=True):
            """Serve a single JPEG frame."""
            jpeg = streamer.get_jpeg()
            if jpeg is None:
                self.send_response(503)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'No frame available')
                return

            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(jpeg)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(jpeg)

        def _handle_index(self):
            """Simple HTML page with embedded stream."""
            secret_param = f'?secret={streamer.secret}' if streamer.secret else ''
            html = f'''<!DOCTYPE html>
<html><head><title>Snarling Thermal Cam</title>
<style>body{{background:#111;color:#eee;font-family:monospace;text-align:center;margin-top:2em}}
img{{border:2px solid #333;max-width:100%}}</style></head>
<body>
<h1>Snarling Thermal Cam</h1>
<img src="/thermal/stream{secret_param}" alt="Thermal stream">
<p>24x32 MLX90640 | 2 FPS | Live</p>
</body></html>'''.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):
            logger.debug(f"[thermal_stream] {args[0] if args else ''}")

    return ThermalStreamHandler


# --- Standalone mode (for testing without snarling) ---

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    print("[thermal_stream] Standalone mode — generating test pattern")
    print("[thermal_stream] Set THERMAL_STREAM_SECRET env var for auth")
    print(f"[thermal_stream] Starting on port {os.environ.get('THERMAL_STREAM_PORT', '5001')}")

    # Create a fake thermal sensor for standalone testing
    class FakeThermal:
        def latest_frame(self):
            """Generate a slowly-changing test pattern."""
            import math
            t = time.time()
            rows, cols = 32, 24
            rotated = []
            for r in range(rows):
                for c in range(cols):
                    # Warm blob moving across the frame
                    cx = 12 + 8 * math.sin(t * 0.3)
                    cy = 16 + 6 * math.cos(t * 0.2)
                    dist = math.sqrt((c - cx) ** 2 + (r - cy) ** 2)
                    temp = 22.0 + 8.0 * max(0, 1.0 - dist / 8.0)
                    # Ambient gradient
                    temp += (r / rows) * 2.0
                    rotated.append(temp)
            return (rotated, rows, cols)

    # Simulated presence state
    class FakeSnarling:
        _environmental_state = {
            "present": True,
            "proximity": 0.7,
            "proximity_zone": "settled",
            "ambient_temp": 23.4,
        }

    streamer = ThermalStreamer(FakeThermal(), snarling=FakeSnarling())
    streamer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        streamer.stop()