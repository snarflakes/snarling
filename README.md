# Snarling 🐾

<img width="800" height="800" alt="Snarling" src="https://github.com/user-attachments/assets/6a5f9196-0bcd-40ef-b045-28a8a6755dbb" />

**A physical status companion for OpenClaw agents.** A Raspberry Pi-powered display that lives on your desk and shows what your AI is doing — even when you're not looking at a screen.

Inspired by [Pwnagotchi](https://pwnagotchi.ai/), built for OpenClaw.

## What It Does

Snarling is a tiny creature on a tiny screen. It reacts to your agent's state in real time — sleeping when idle, focused when processing, chatty when responding, and alert when it needs your approval on something.

Instead of checking your phone or terminal to see if your agent is working, resting, or needs attention — just glance at Snarling.

## States & Faces

| State | Face | When |
|-------|------|------|
| **Sleeping** | `(⇀‿‿↼)` | Agent is idle |
| **Processing** | `(◕‿‿◕)` | Agent is using tools / thinking |
| **Communicating** | `(ᵔ◡◡ᵔ)` | Agent is generating a response |
| **Error** | `(╥☁╥ )` | Something went wrong |
| **Awaiting Approval** | `( ⚆_⚆)` | Agent needs your yes/no decision |

Each state has its own color, LED pattern, and animation — breathing blue when sleeping, pulsing melon when processing, flashing red when approval is needed.

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
└─────────────┘                    └──────────────┘                  └──────────────┘
```

**How it works:**

1. The [OpenClaw Interaction Bridge plugin](https://github.com/snarflakes/openclaw-interaction-bridge) watches your agent's activity
2. It POSTs state changes to Snarling's `/state` endpoint on port 5000
3. It POSTs approval requests to Snarling's `/approval/alert` endpoint (direct, no middleman)
4. Snarling updates the display, LED, and face expression in real time
5. When you press A (approve) or B (reject), Snarling POSTs the decision back to the OpenClaw gateway
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
| `/approval/alert` | POST | Display an approval request on screen |
| `/counts` | GET | Get lifetime approve/reject counts |
| `/health` | GET | Health check |

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