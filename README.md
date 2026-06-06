# Snarling

<img width="800" height="800" alt="snarling" src="https://github.com/user-attachments/assets/8683255b-94bc-4f6f-b858-64c81cc94ddc" />

**A physical status companion for OpenClaw agents.** A Raspberry Pi-powered display that lives on your desk and shows what your AI is doing — even when you're not looking at a screen. It senses your presence with a thermal camera, lets your agent send you notifications with a feedback loop, and can ask for your approval with physical buttons.

Inspired by [Pwnagotchi](https://pwnagotchi.ai/), built for OpenClaw.

## Live Demo (no hardware required)

Try the interaction loop in your browser:

👉 https://snarflakes.github.io/snarling/demo/

## What It Does

Snarling is a tiny creature on a tiny screen. It reacts to your agent's state in real time — sleeping when idle, focused when processing, chatty when responding, and alert when it needs your approval on something. It can also nudge you with notifications — subtle at first, revealing more when you press A — and report back whether you actually read them, enabling notification attunement.

Instead of checking your phone or terminal to see if your agent is working, resting, or needs attention — just glance at Snarling.

## States & Faces

| State | Face | When |
|-------|------|------|
| **Sleeping** | `(⇀‿‿↼)` | Agent is idle |
| **Processing** | `(◕‿‿◕)` | Agent is using tools / thinking |
| **Communicating** | `(ᵔ◡◡ᵔ)` | Agent is generating a response |
| **Error** | `(╥☁╥ )` | Something went wrong |
| **Awaiting Approval** | `( ⚆_⚆)` | Agent needs your yes/no decision |
| **Notification** | `(◕‿‿◕)` | Agent has something to tell you |
| **Proximity Aware** | `(≖◡◡≖)` | Someone is nearby while agent is sleeping (thermal sensor) |
| **Leaving** | `(◡‿◡)` | Someone is walking away (thermal sensor, ~500ms) |
| **Listening** | `(◕‿‿◕)` teal | Voice recording in progress (X button) |

Each state has its own color, LED pattern, and animation — breathing blue when sleeping, pulsing melon when processing, flashing red when approval is needed.

## Notifications

Snarling also handles **notifications** — informational alerts from your agent that don't require a decision. Unlike approvals, notifications are subtle:

- The creature's face changes to a notification expression (colored by priority)
- The 5 status boxes at the top fill based on priority (1 for low, 3 for normal, 5 for high) and change color
- The LED pulses in the priority's color (warm orange for high, yellow for normal, soft yellow for low)
- **No text is shown until you press A** — the notification stays as a subtle visual presence on the creature's face

### Notification Interaction

| Button | Action |
|--------|--------|
| **A** | Reveal notification text (cycles through banners) |
| **B** | Dismiss without reading |
| (no press) | Auto-dismiss after timeout (low priority only) |

When you press A, the notification text appears below a separator line in 2–3 rotating banners (priority header + short preview, then full message across 2 content banners). The creature's expression shifts subtly to acknowledge you're reading.

### Priority-Based Timeouts

| Priority | LED Color | Status Boxes | Timeout |
|----------|-----------|-------------|---------|
| **high** | Warm orange | 5/5 filled | None — stays until you interact |
| **normal** | Yellow | 3/5 filled | None — stays until you interact |
| **low** | Soft yellow | 1/5 filled | 300s — auto-dismisses, sends `timed_out` feedback |

No urgent or moderate notification should ever just disappear. Only low-priority notifications auto-dismiss.

### Notification Feedback Loop

When you interact with a notification (reveal, dismiss, or let it time out), Snarling sends feedback back to the OpenClaw gateway:

```json
{
  "notification_id": "notify-1234567890-abc",
  "revealed": true,
  "time_to_reveal_sec": 42.5,
  "dismissed": false,
  "timed_out": false,
  "secret": "uuid",
  "sessionKey": "agent:main:main"
}
```

`time_to_reveal_sec` measures the total time from when the notification was **sent** to when you interacted with it — including any time spent queued behind other notifications. This enables **notification attunement**: the agent learning what kinds of messages you respond to, and when.

If a notification is queued behind another one (e.g., a normal-priority notification is already on screen), it waits in a priority-sorted stack. When the first notification is resolved, the next one appears automatically.

### Notification Banners

Messages are word-wrapped across up to 3 banners that cycle every ~1.5 seconds:

1. **Banner 1**: Priority header (e.g., `!! HIGH`, `* MODERATE`, `~ LOW`) + short preview
2. **Banner 2**: Full message (up to 3 lines of wrapped text)
3. **Banner 3**: Continuation for longer messages (only if needed)

Short messages get 2 banners. Longer messages get all 3.

## Voice Input (X Button)

Snarling has a built-in microphone input flow via the **X button**. When pressed in normal state (not during an approval or notification), Snarling:

1. Enters **listening** state (teal face, pulsing fill bars, `🎙 Listening...` message)
2. Records 20 seconds of audio locally via `arecord` (no gateway dependency for recording)
3. Switches to **processing** state (`⏱ Thinking...`) while POSTing the WAV path to the plugin's `/transcribe-and-reply` endpoint
4. The plugin transcribes the audio and injects it as a voice system event into the agent's session
5. On success, the plugin drives state transitions back to sleeping via the `/state` API
6. On failure, Snarling falls back to sleeping

The mic is checked at startup — if no audio input device is found (`plughw:3,0`), the X button shows `No mic found` instead of starting a recording. The WAV file is cleaned up after 60 seconds to give the plugin time to read it.

## Physical Approvals

Snarling isn't just a display — it's an input device. When your agent needs approval for an action (deleting a file, sending a message, etc.), Snarling enters **awaiting approval** state and shows the request on screen.

### Approval Queueing

Multiple approvals no longer overwrite each other. If an approval is already on screen when a new one arrives, the new one is queued behind it (FIFO). When the current approval is resolved (approved or rejected), the next one in the queue is automatically displayed. Each queued approval carries its own `session_key` so the A/B callback always routes to the correct agent session, even when two different agents queue approvals simultaneously.

This also means approvals take priority over notifications — if a notification is on screen when an approval arrives, the notification is bumped back to the notification stack. After the last approval is resolved, queued notifications reappear automatically.

### Notification Priority Bumping

Notifications are held in a priority-sorted stack (high → normal → low, newest-first within same priority). If a higher-priority notification arrives while a lower one is on screen and the user hasn't revealed it yet, the higher-priority one bumps the current notification back to the stack and takes over. Once resolved, the stack shows the next highest-priority notification.

| Button | Normal Mode | Approval Mode | Notification Mode |
|--------|-------------|---------------|-------------------|
| **A** | Show status summary | ✅ **Approve** | Reveal text (1st press) → Accept (2nd press) |
| **B** | (unused) | ❌ **Reject** | Dismiss without reading |
| **X** | 🎙 **Record voice** | — | — |
| **Y** | Toggle sleep mode | — | — |

When you press A or B, Snarling POSTs the decision or feedback back to the OpenClaw gateway's `/approval-callback` or `/notification-callback` routes, then sends a WebSocket RPC wake so the agent picks up the result immediately (~5 seconds total latency).

## Thermal Presence Detection

Snarling can detect human presence using an [MLX90640 thermal camera](https://melexis.com/en/product/mlx90640) mounted nearby. When connected, Snarling:

- Detects when someone arrives or leaves (binary presence)
- Tracks proximity zones (absent → approaching → present) for face expression changes
- Posts presence change events to the OpenClaw gateway so your agent knows you're there
- Gracefully degrades — if the camera isn't available, everything else works identically

### How It Works

The MLX90640 reads a 32×24 thermal grid at ~4 Hz. The `ThermalSensor` class (in `thermal.py`) runs as a daemon thread:

1. **Frame processing**: Each frame computes ambient temperature, identifies warm blobs (≥3°C above ambient, ≥15 pixels, with aspect ratio filtering to reject oven heat plumes)
2. **Dual debounce**: Fast path (2-frame) for face expressions and LED brightness, slow path (15-frame ≈ 3.75s) for presence callbacks and gateway events — avoids flicker on the display while keeping presence data stable
3. **Proximity**: Calculated from blob size and warmth — larger, warmer blobs mean closer proximity
4. **Callbacks**: `on_presence_change(was_absent, now_present, ambient_temp)` and `on_proximity_change(old_zone, new_zone, proximity, ambient_temp)` fire on the slow debounce path, `on_display_zone_change(old_zone, new_zone, proximity, ambient_temp)` fires on the fast path for immediate face/LED reactivity

### Presence Session Tracking

Each presence session (from arrival to departure) tracks:
- **Dwell time**: how long someone was present
- **Proximity peak**: highest proximity value during the session
- **Zone flips**: number of proximity zone transitions (approaching ↔ present)
- **Approach time**: seconds from first "approaching" zone to settled presence

On departure, this data is logged to `/tmp/presence-log.jsonl` as a compact JSON line with `dwell_sec`, `prox_peak`, and `zone_flips` fields. On `presence_settled` (60s of stable presence), the approach time and zone flips are included.

### Presence Events to OpenClaw

Snarling sends two types of presence events to the OpenClaw gateway's `/environmental-event` route:

#### `presence_change` (arrival/departure)

Fired immediately when thermal detection confirms someone arrived or left:

```json
{
  "type": "presence_change",
  "present": true,
  "absent_duration": "3h20m",
  "absent_duration_sec": 12000,
  "timestamp": 1714588800
}
```

- `present`: `true` for arrival, `false` for departure
- `absent_duration`: human-readable string on return (e.g. `"45s"`, `"3m20s"`, `"2h15m"`), `null` on departure
- `absent_duration_sec`: numeric seconds (only updated for absences ≥ 60s to avoid short-gap noise)
- `timestamp`: Unix epoch when the change was detected

#### `presence_settled` (stable presence)

Fired 60 seconds after confirmed arrival — means someone has been present long enough to be considered "settled":

```json
{
  "type": "presence_settled",
  "absent_duration": "2h15m",
  "absent_duration_sec": 8100,
  "timestamp": 1714588860
}
```

This is the signal the agent uses for "someone is home and staying" — useful for proactive check-ins, greeting messages, or adjusting notification behavior.

These events are routed to the agent via the OpenClaw Interaction Bridge plugin (see Configuration below). The plugin handles V1/V2 compatibility — `presence_settled` events without a `trigger_reason` field are treated as `observation_report` with `trigger_reason: "presence_settled"`.

Proximity zone changes are **not** sent to the gateway — they're used internally by Snarling for face expressions and LED brightness only.

### Presence Data Logging

All presence events are logged to `/tmp/presence-log.jsonl` as compact JSON lines:

```json
{"ts":1714588800,"type":"presence_change","p":1,"abs":12000}
{"ts":1714588860,"type":"presence_settled","abs":8100,"approach_sec":3.2,"prox_peak":0.85,"zone_flips":4}
{"ts":1714589200,"type":"presence_change","p":0,"dwell_sec":665.0,"prox_peak":0.92,"zone_flips":7}
```

Fields: `ts` (epoch), `type`, `p` (1=present, 0=absent), `abs` (absent seconds), `dwell_sec` (departure only), `approach_sec` (settled only), `prox_peak`, `zone_flips`.

This log enables the environmental agent to build rolling statistics over time — average dwell times, nocturnal patterns, peak proximity trends — without needing to parse full event payloads.

### Configuration

| Setting | Where | Default | Description |
|----------|-------|---------|-------------|
| `ENVIRONMENTAL_EVENTS_ENABLED` | `snarling.py` | `True` | Master switch — set to `False` to stop posting events |
| `ENVIRONMENTAL_SESSION_KEY` | Gateway env var | `""` (empty) | Routes presence events to a specific agent session. Empty = events acknowledged but dropped. Set to `"agent:main:main"` for the orchestrator, or `"session:environmental"` for a dedicated environmental agent |

### Known Issues

- **Oven heat plume**: The double oven in the kitchen creates a rising heat column that the MLX90640 sees as a person-shaped warm blob (up to 70°C). Only happens when the oven is on. Temperature alone can't distinguish it from a person.
- **Pi housing heat**: The Raspberry Pi housing surface appears in the MLX90640's field of view (bottom-left of rotated frame, rows 12–17, cols 13–15) as a persistent warm zone (~30–40°C). Not a laptop base — it's the Pi case itself.

## Architecture

```
┌────────────┐     HTTP POST      ┌────────────┐   button press    ┌────────────┐
│  OpenClaw    │ ───────────────── │  Snarling     │ ────────────────┠ │  OpenClaw    │
│  (plugin)    │   /state (5000)   │  Display      │  webhook + WS    │  Gateway     │
│              │ ───────────────── │  + Buttons    │  wake           │              │
│              │   /approval/alert  │  + Thermal    │                  │              │
│              │ ───────────────── │  + Mic        │ ──────────────┠ │              │
│              │   /approval/alert  │               │  /approval-cb    │              │
│              │   (type: notify)  │               │ ──────────────┠ │              │
│              │                    │               │  /notification-cb │              │
│              │                    │               │                  │              │
│              │                    │ ────────────────────────────────┠ │              │
│              │                    │  /environmental-event            │              │
│              │                    │  (presence_change + settled)     │              │
│              │                    │                                  │              │
│              │                    │  X button press:                 │              │
│              │                    │  🎙 arecord (local) ────────────┠ │              │
│              │                    │  20s WAV → plugin transcribes    │  /transcribe  │
└────────────┘                    └───────────┘                  └───────────┘
```

**How it works:**

1. The [OpenClaw Interaction Bridge plugin](https://github.com/snarflakes/openclaw-interaction-bridge) watches your agent's activity
2. It POSTs state changes to Snarling's `/state` endpoint on port 5000
3. It POSTs approval requests and notifications to Snarling's `/approval/alert` endpoint (direct, no middleman)
4. Snarling updates the display, LED, and face expression in real time
5. When you press A (approve/reveal) or B (reject/dismiss), Snarling POSTs the decision or feedback back to the OpenClaw gateway
6. Snarling sends a WebSocket RPC wake to bypass the gateway's `requests-in-flight` check
7. When the thermal sensor detects presence changes, Snarling POSTs to the gateway's `/environmental-event` route
8. **X button** starts local audio recording (`arecord`, 20s), then POSTs the WAV path to the plugin's `/transcribe-and-reply` endpoint for transcription and agent injection

### Components

| File | Purpose |
|------|----------|
| `snarling.py` | Main creature — display rendering, face animations, button handling, Flask server, voice input, approval/notification queueing, thermal callbacks, environmental event posting, presence session tracking, data logging |
| `thermal.py` | ThermalSensor class — MLX90640 daemon thread, frame processing, blob detection, dual debounce (fast display / slow gateway), presence/proximity callbacks |

## Hardware

- **Display:** [Pimoroni Display HAT Mini](https://shop.pimoroni.com/products/display-hat-mini) (320×240 IPS)
- **Computer:** Raspberry Pi 4 (recommended)
- **Case:** Argon One V2 (fits nicely, keeps it cool)
- **Header Adapter:** [for getting the angle right on the display](https://www.adafruit.com/product/2823)
- **Thermal Camera:** [MLX plugs right into header](https://www.adafruit.com/product/4469)
- **StemmaQT Cable:** [need this cable to plug camera into the display for power and data](https://www.adafruit.com/product/4210)

![IMG_8292](https://github.com/user-attachments/assets/a3d8e3e8-a689-4948-94ee-bfa1f3cf6c29)

## Setup

### 1. Install Snarling

Buy a screen for your raspberry pi. Install the python display library specific to your screen. You will have to have openclaw adapt Snarling if you use a different screen.

```bash
git clone https://github.com/snarflakes/snarling.git
cd snarling

# Install dependencies
pip install flask pillow requests websocket-client mlx90640

# Copy the systemd service file to enable auto-start
sudo cp snarling.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable snarling.service
# To start now: sudo systemctl start snarling.service

# To run manually for testing (Ctrl+C to stop):
python snarling.py
```

The service file handles auto-restart on crash/kill.

### 2. Install the Interaction Bridge Plugin

Snarling needs the [openclaw-interaction-bridge](https://github.com/snarflakes/openclaw-interaction-bridge) plugin to receive state updates and send approval responses.

[![GitHub Repo](https://img.shields.io/badge/GitHub-openclaw--interaction--bridge-blue?logo=github)](https://github.com/snarflakes/openclaw-interaction-bridge)

### 3. Configure Your Agent

Add this to your agent's context (or system prompt):

> You are now running with the OpenClaw Interaction Bridge plugin enabled.
> 
> - Bridge plugin installed at `~/.openclaw/extensions/openclaw-interaction-bridge`
> - Snarling display hardware ready and polling for status
> - State updates POST to `http://localhost:5000/state`
> - Approval alerts POST to `http://localhost:5000/approval/alert`

### 4. Verify

Check the display updates when you:
- Run a tool → shows **processing**
- Generate a response → shows **communicating**
- Wait 10 seconds → shows **sleeping**
- Request user approval → shows **awaiting approval** with the request on screen

## API Endpoints

Snarling runs a Flask server on port 5000:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/state` | POST | Set creature state (`sleeping`, `processing`, `communicating`, `error`) |
| `/approval/alert` | POST | Display an approval request or notification on screen |
| `/counts` | GET | Get lifetime approve/reject counts |
| `/health` | GET | Health check |
| `/status` | GET | Get current state, notification stack, and active notification details |
| `/presence` | GET | Get current thermal presence state (present, proximity, proximity_zone, ambient_temp, last_change) |
| `/environment` | POST | Update environmental state from external sources (rejected when thermal sensor is active) |

> The approval server on port 5001 (`approval_server.py`) has been removed. The plugin talks directly to Snarling on port 5000.

## Development

Push to `development`, merge through `main`.

```bash
git checkout development
git add .
git commit -m "feat: description"
git push origin development
```

## Credits

- Inspired by [Pwnagotchi](https://pwnagotchi.ai/) — the idea of a tiny creature that reacts to what's happening
- Born from [Dustytext](https://dustytext.com), a blockchain world-building experiment
- Built by [Snar](https://github.com/snarflakes)
