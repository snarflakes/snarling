"""Thermal camera view renderer for Snarling display.

Renders a live heat map from MLX90640 sensor data on the 240x240 HAT display.
This file is optional — if deleted, snarling.py will disable the thermal view toggle
but continue working normally.

Usage from snarling.py:
    from thermal_view import draw_thermal_view
    draw_thermal_view(draw, WIDTH, HEIGHT, frame_data)

Where frame_data = (rotated_list, rows, cols) from ThermalSensor.latest_frame.
"""


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
        if r > 0 and mask[r-1][c] and not visited[r-1][c]: stack.append((r-1, c))
        if r < rows-1 and mask[r+1][c] and not visited[r+1][c]: stack.append((r+1, c))
        if c > 0 and mask[r][c-1] and not visited[r][c-1]: stack.append((r, c-1))
        if c < cols-1 and mask[r][c+1] and not visited[r][c+1]: stack.append((r, c+1))
    return blob


def _temp_to_color(temp, t_min, t_max):
    """Map a temperature to an RGB color using a perceptual gradient."""
    if t_max <= t_min:
        t_max = t_min + 1.0
    t = max(0.0, min(1.0, (temp - t_min) / (t_max - t_min)))
    if t < 0.25:
        f = t / 0.25
        return (int(20 + 80*f), 0, int(180 + 40*f))
    elif t < 0.5:
        f = (t - 0.25) / 0.25
        return (int(100 - 80*f), int(180*f), int(220 - 120*f))
    elif t < 0.75:
        f = (t - 0.5) / 0.25
        return (int(20 + 200*f), int(180 + 75*f), int(100 - 80*f))
    else:
        f = (t - 0.75) / 0.25
        return (int(220 + 35*f), int(255 - 80*f), int(20 + 180*f))


def draw_thermal_view(draw, width, height, frame_data, font=None):
    """Render a thermal camera heat map on the display.

    Args:
        draw: PIL ImageDraw object
        width: Display width (e.g. 240)
        height: Display height (e.g. 240)
        frame_data: Tuple of (rotated, rows, cols) from ThermalSensor.latest_frame,
                    or None if no frame is available yet.
        font: Optional PIL font for text overlays. If None, attempts to load
              DejaVuSansMono-Bold, falls back to default.

    Returns:
        True if rendered successfully, False if no frame data (caller should
        show a "waiting" message or disable thermal view).
    """
    from PIL import ImageFont

    if frame_data is None:
        return False

    rotated, rows, cols = frame_data

    if font is None:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 18)
        except OSError:
            font = ImageFont.load_default()

    # Compute ambient and threshold
    EDGE_MARGIN = 2
    interior_temps = []
    for r in range(EDGE_MARGIN, rows - EDGE_MARGIN):
        row_offset = r * cols
        for c in range(EDGE_MARGIN, cols - EDGE_MARGIN):
            interior_temps.append(rotated[row_offset + c])
    interior_temps.sort()
    ambient = interior_temps[len(interior_temps) // 2]

    # Adaptive threshold (same logic as thermal.py)
    if ambient < 25:
        threshold = ambient + 3.0
    elif ambient < 30:
        threshold = ambient + 2.0
    else:
        threshold = ambient + 1.5

    # Color range for display
    all_temps = list(rotated)
    t_min = min(all_temps)
    t_max = max(all_temps)
    t_range = max(t_max - t_min, 2.0)
    display_min = t_min - t_range * 0.05
    display_max = t_max + t_range * 0.05

    # Build binary mask for warm pixels
    mask = [[False] * cols for _ in range(rows)]
    for r in range(EDGE_MARGIN, rows - EDGE_MARGIN):
        row_offset = r * cols
        for c in range(EDGE_MARGIN, cols - EDGE_MARGIN):
            if rotated[row_offset + c] > threshold:
                mask[r][c] = True

    # Find blobs for highlighting
    visited = [[False] * cols for _ in range(rows)]
    person_blobs = []
    for r in range(rows):
        for c in range(cols):
            if mask[r][c] and not visited[r][c]:
                blob = _flood_fill(mask, rows, cols, r, c, visited)
                if len(blob) >= 15:  # MIN_PERSON_PIXELS
                    min_br = min(p[0] for p in blob)
                    max_br = max(p[0] for p in blob)
                    min_bc = min(p[1] for p in blob)
                    max_bc = max(p[1] for p in blob)
                    person_blobs.append((min_br, min_bc, max_br, max_bc, blob))

    # Cell scale for heat map
    CELL_SCALE = min(width / cols, height / rows)
    hm_draw_w = int(cols * CELL_SCALE)
    hm_draw_h = int(rows * CELL_SCALE)
    hm_offset_x = (width - hm_draw_w) // 2

    # Clear screen
    draw.rectangle((0, 0, width, height), fill=(0, 0, 0))

    # Render heat map
    for r in range(rows):
        for c in range(cols):
            temp = rotated[r * cols + c]
            color = _temp_to_color(temp, display_min, display_max)

            # Col -> X (centered), Row -> Y (inverted: row 0 = bottom)
            x0 = int(c * CELL_SCALE) + hm_offset_x
            y0 = int((rows - 1 - r) * CELL_SCALE)
            x1 = int((c + 1) * CELL_SCALE) + hm_offset_x
            y1 = int((rows - r) * CELL_SCALE)

            draw.rectangle((x0, y0, x1, y1), fill=color)

    # Highlight warm pixels with bright outline
    for r in range(rows):
        for c in range(cols):
            if mask[r][c]:
                x0 = int(c * CELL_SCALE) + hm_offset_x
                y0 = int((rows - 1 - r) * CELL_SCALE)
                x1 = int((c + 1) * CELL_SCALE) + hm_offset_x
                y1 = int((rows - r) * CELL_SCALE)
                draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 255), width=1)

    # Draw person blob rectangles
    for min_br, min_bc, max_br, max_bc, _blob in person_blobs:
        bx0 = int(min_bc * CELL_SCALE) + hm_offset_x - 1
        by0 = int((rows - 1 - max_br) * CELL_SCALE) - 1
        bx1 = int((max_bc + 1) * CELL_SCALE) + hm_offset_x + 1
        by1 = int((rows - min_br) * CELL_SCALE) + 1
        draw.rectangle((bx0, by0, bx1, by1), outline=(255, 80, 80), width=2)

    # Ambient text overlay
    draw.text((5, 5), f"{ambient:.1f}C", fill=(255, 255, 255), font=font)
    draw.text((5, 25), f"Thr:{threshold:.1f}C", fill=(200, 200, 200), font=font)

    # Exit hint
    draw.text((width - 80, height - 20), "Y:exit", fill=(180, 180, 180), font=font)

    return True