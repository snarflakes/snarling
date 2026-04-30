# Snarling

<img width="800" height="800" alt="snarling" src="https://github.com/user-attachments/assets/8683255b-94bc-4f6f-b858-64c81cc94ddc" />

**A physical status companion for OpenClaw agents.** A Raspberry Pi-powered display that lives on your desk and shows what your AI is doing — even when you're not looking at a screen. It also lets your agent send you notifications with a feedback loop, so it can learn when and how to reach out.

Inspired by [Pwnagotchi](https://pwnagotchi.ai/), built for OpenClaw.

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

## Physical Approvals

Snarling isn't just a display — it's an input device. When your agent needs approval for an action (deleting a file, sending a message, etc.), Snarling enters **awaiting approval** state and shows the request on screen.

| Button | Normal Mode | Approval Mode |
|--------|-------------|---------------|
| **A** | Show status summary | ✅ **Approve** |
| **B** | Trigger heartbeat | ❌ **Reject** |
| **X** | Toggle sleep mode | — |
| **Y** | Toggle mute mode | — |

When you press A or B, Snarling POSTs the decision back to the OpenClaw gateway's `/approval-callback` route, then sends a WebSocket RPC wake so the agent picks up the result immediately (~5 seconds total latency).

## Architecture

```
┌─────────────┐     HTTP POST      ┌──────────────┐   button press    ┌──────────────┐
│  OpenClaw    │ ────────────────── │  Snarling     │ ───────────────► │  OpenClaw    │
│  (plugin)    │   /state (5000)   │  Display      │  webhook + WS    │  Gateway     │
│              │ ────────────────── │  + Buttons    │  wake           │              │
│              │   /approval/alert  │               │                  │              │
│              │ ────────────────── │               │ ───────────────► │              │
│              │   /approval/alert  │               │  /approval-cb    │              │
│              │   (type: notify)   │               │ ───────────────► │              │
│              │                    │               │  /notification-cb │              │
└─────────────┘                    └──────────────┘                  └──────────────┘
```

**How it works:**

1. The [OpenClaw Interaction Bridge plugin](https://github.com/snarflakes/openclaw-interaction-bridge) watches your agent's activity
2. It POSTs state changes to Snarling's `/state` endpoint on port 5000
3. It POSTs approval requests and notifications to Snarling's `/approval/alert` endpoint (direct, no middleman)
4. Snarling updates the display, LED, and face expression in real time
5. When you press A (approve/reveal) or B (reject/dismiss), Snarling POSTs the decision or feedback back to the OpenClaw gateway
6. Snarling sends a WebSocket RPC wake to bypass the gateway's `requests-in-flight` check

### Components

| File | Purpose |
|------|---------|
| `snarling.py` | Main creature — display rendering, face animations, button handling, Flask server for state/approval API, WebSocket wake on approval |

## Hardware

- **Display:** [Pimoroni Display HAT Mini](https://shop.pimoroni.com/products/display-hat-mini) (320×240 IPS)
- **Computer:** Raspberry Pi 4 (recommended)
- **Case:** Argon One V2 (fits nicely, keeps it cool)
- **Rotation:** 180° (configured in software)
![IMG_8292](https://github.com/user-attachments/assets/a3d8e3e8-a689-4948-94ee-bfa1f3cf6c29)

## Setup

### 1. Install Snarling

Buy a screen for your raspberry pi. Install the python display library specific to your screen. You will have to have openclaw adapt Snarling if you use a different screen.

```bash
git clone https://github.com/snarflakes/snarling.git
cd snarling

# Install dependencies
pip install flask pillow requests websocket-client

# Snarling is managed by myscript — auto-restarts on crash/kill
# To run manually for testing:
python snarling.py
```

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
