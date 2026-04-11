# Snarling 🐾

<img width="800" height="800" alt="ChatGPT Image Apr 8, 2026, 04_43_57 PM" src="https://github.com/user-attachments/assets/6a5f9196-0bcd-40ef-b045-28a8a6755dbb" />

A physical status companion for OpenClaw agents — a Raspberry Pi-powered display that shows what your AI is up to, even when you're not watching the chat.

## What It Is

Snarling is a small screen attached to your OpenClaw host that provides ambient awareness of your agent's activity. Instead of checking your phone to see if the AI is working, resting, or needs attention, you can glance at the Snarling display. Easily customize with your own screen.  Argon-one housing and Raspberry Pi 4 used for easy configuration! + a pimoroni display HAT mini https://shop.pimoroni.com/products/display-hat-mini?variant=39496084717651.

![IMG_8292](https://github.com/user-attachments/assets/a3d8e3e8-a689-4948-94ee-bfa1f3cf6c29)

## Features

### Status Display
- **Active indicator** — Shows when your agent is talking, researching, or resting

### Interactive Buttons
| Button | Short Press | Long Press |
|--------|-------------|-----------|
| X | Turn off display

### Future Ideas
- **Approval bridge** — Physically approve/reject agent actions via buttons
- **Motion detection** — Wake display when you approach
- **Audio feedback** — Gentle sounds for status changes
- **Thermal camera** — Detect presence and room temperature

## Architecture

Snarling runs as a service on your Raspberry Pi (or similar host) and communicates with OpenClaw via:

- **HTTP bridge** (default) — Polls the Mission Control API
- **State files** (fallback) — Reads local JSON if no Mission Control
- **Manual controls** — Physical buttons for interaction

### Default Mode (with Mission Control)
The Interaction Bridge plugin POSTs status to `http://localhost:3000/api/status`. Snarling polls this endpoint to display current agent state.

### Fallback Mode (no Mission Control)
Configure the bridge to write to `/home/pi/snarling/state.json` instead. Snarling reads the local file directly.

## Repos

- **snarling** — This repo (hardware display, button handling)
- **openclaw-interaction-bridge** — Plugin for OpenClaw side integration [![GitHub Repo](https://img.shields.io/badge/GitHub-openclaw--interaction--bridge-blue?logo=github)](https://github.com/snarflakes/openclaw-interaction-bridge)

## Setup

Buy a screen for your raspberry pi.  Install the python display library specific to your screen. You will have to have openclaw adapt Snarling if you use a different screen.

```bash
# Clone Snarling repo
git clone https://github.com/snarflakes/snarling.git
cd snarling
# Install service
sudo cp snarling.service /etc/systemd/system/
sudo systemctl enable snarling
sudo systemctl start snarling
```

- For updated agent status, you need to install the companion openclaw-interaction-bridge plugin. Found here: [![GitHub Repo](https://img.shields.io/badge/GitHub-openclaw--interaction--bridge-blue?logo=github)](https://github.com/snarflakes/openclaw-interaction-bridge)

- Lastly you need to build the status "host" which stores the status from the openclaw-interaction-bridge.  Add it to your own mission-control, either way feed this prompt to your agent:
```
You are now running with the OpenClaw Interaction Bridge plugin enabled.

## What's Already Set Up

- Interaction Bridge plugin installed at `~/.openclaw/extensions/openclaw-interaction-bridge`
- Snarling display hardware ready and polling for status
- Mission Control API running at `http://localhost:3000/api/status`

## What Happens Automatically

The bridge watches your OpenClaw activity and reports state changes:

| Event | Status Sent | Snarling Shows |
|-------|-------------|----------------|
| You start using tools | `processing` | Working indicator |
| You begin replying | `speaking` | Active/talking |
| 30 seconds idle | `idle` | Resting state |

## For Users WITH Mission Control

Snarling should poll: `http://your-pi-ip:3000/api/status`

Mission Control handles the state file and serves it to Snarling.

## For Users WITHOUT Mission Control

If you don't have Mission Control running, configure the bridge to write 
directly to Snarling's state file:

Edit `~/.openclaw/extensions/openclaw-interaction-bridge/index.ts`:

Change:
  const MISSION_CONTROL_URL = "http://localhost:3000/api/status"

To:
  const STATE_FILE_PATH = "/home/pi/snarling/state.json"

Then modify `updateState()` to write JSON to that file instead of HTTP POST.

Snarling will read the local file directly.

## Verify It's Working

Check Snarling display updates when you:
- Run a tool (shows "processing")
- Generate a response (shows "speaking")
- Wait 30 seconds (shows "idle")

```
## Development

Push changes to the `development` branch. Merges go through `main`.

```bash
git checkout development
git add .
git commit -m "feature: description"
git push origin development
```

## Credits

Thanks to the Pwnagotchi project for its wonderful inspiration. Born from Dustytext, built by Snar.
