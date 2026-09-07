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
# Configure snarling with:
#   TTS_COMMAND=/path/to/tts-piper.sh

set -eu

MODEL="${PIPER_VOICE_MODEL:-$HOME/.piper-voices/en_US-amy-medium.onnx}"
PIPER="${PIPER_BIN:-$HOME/.local/bin/piper}"
OUTDIR="${TMPDIR:-/tmp}"

TEXT="$(cat)"
[ -z "$TEXT" ] && exit 0

WAV="$OUTDIR/tts-piper-$$.wav"
PREROLL="$OUTDIR/tts-piper-preroll-$$.wav"
trap 'rm -f "$WAV" "$PREROLL"' EXIT

printf '%s' "$TEXT" | "$PIPER" --model "$MODEL" --output_file "$WAV"

# Pre-roll: 0.6s of silence before speech wakes a suspended Bluetooth sink.
# Without it, BT sinks eat the first ~1s of audio during link renegotiation.
ffmpeg -loglevel error -f lavfi -i anullsrc=r=22050:cl=mono -t 0.6 -sample_fmt s16 "$PREROLL" -y

ffplay -autoexit -nodisp -loglevel error "$PREROLL" 2>/dev/null
ffplay -autoexit -nodisp -loglevel error "$WAV" 2>/dev/null
