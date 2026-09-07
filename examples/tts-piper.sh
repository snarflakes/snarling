#!/bin/bash
# tts-piper.sh — reference TTS_COMMAND implementation for snarling notifications.
#
# Contract: read message text from STDIN, render to speech, play it, and block
# until playback finishes. snarling writes the (already-cleaned) text to stdin
# and waits up to 120s for this script to exit.
#
# Uses piper (https://github.com/rhasspy/piper) for offline neural TTS and
# ffplay for playback.
#
# Voice model: set PIPER_VOICE_MODEL to a piper .onnx voice file, or download
# the default (English, amy-medium) once:
#
#   mkdir -p ~/.piper-voices
#   curl -L -o ~/.piper-voices/en_US-amy-medium.onnx \
#     https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
#   curl -L -o ~/.piper-voices/en_US-amy-medium.onnx.json \
#     https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
#
# Install piper to ~/.local/bin/piper and make sure ffmpeg/ffplay are installed.
#
# Output device: playback goes to the system default sink (PipeWire/WirePlumber).
#   USB/HDMI/analog speakers: just plug in and set as default (wpctl set-default <id>).
#   Bluetooth speakers: keep the pre-roll (below) — see TTS_PREROLL.
#
# TTS_PREROLL: seconds of silence played before speech. Bluetooth sinks suspend
#   when idle and eat the first ~1s of audio during link renegotiation, so BT
#   users want 0.6 (the default). USB/HDMI/analog output has no such handshake —
#   set TTS_PREROLL=0 for instant playback.
#
# Configure snarling with:
#   TTS_COMMAND=/path/to/tts-piper.sh

set -eu

MODEL="${PIPER_VOICE_MODEL:-$HOME/.piper-voices/en_US-amy-medium.onnx}"
PIPER="${PIPER_BIN:-$HOME/.local/bin/piper}"
OUTDIR="${TMPDIR:-/tmp}"

TEXT="$(cat)"
[ -z "$TEXT" ] && exit 0

WAV="$OUTDIR/tts-piper-$$.wav"
trap 'rm -f "$WAV" "$PREROLL" 2>/dev/null' EXIT

printf '%s' "$TEXT" | "$PIPER" --model "$MODEL" --output_file "$WAV"

PREROLL_SEC="${TTS_PREROLL:-0.6}"   # BT sinks: 0.6; USB/HDMI/analog: set 0
if [ "$PREROLL_SEC" != "0" ]; then
  PREROLL="$OUTDIR/tts-piper-preroll-$$.wav"
  # Pre-roll: silence wakes a suspended Bluetooth sink so speech isn't clipped.
  ffmpeg -loglevel error -f lavfi -i anullsrc=r=22050:cl=mono -t "$PREROLL_SEC" -sample_fmt s16 "$PREROLL" -y
  ffplay -autoexit -nodisp -loglevel error "$PREROLL" 2>/dev/null
fi

ffplay -autoexit -nodisp -loglevel error "$WAV" 2>/dev/null
