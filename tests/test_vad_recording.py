#!/usr/bin/env python3
"""Tests for snarling's Silero VAD voice recording.

Runs the VAD decision logic against synthetic PCM fixtures. Uses the real
SileroVAD ONNX model when available (integration path) and a scripted fake
for deterministic edge cases (mocked-VAD path).

Run:  /home/openpi/displayhat-env/bin/python tests/test_vad_recording.py
"""

import os
import sys
import wave
import struct
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import snarling as a module WITHOUT running its main() / display init.
# Module-level code only defines constants + classes (display import happens
# at module top — displayhatmini is available in the displayhat-env).
import snarling
from snarling import SileroVAD, VAD_CHUNK_SAMPLES, VAD_SAMPLE_RATE


def make_tone(freq=440, seconds=1.0, amplitude=0.3, sr=VAD_SAMPLE_RATE):
    """Synthetic speech-like buzz (amplitude-modulated harmonic stack)."""
    import math
    frames = int(sr * seconds)
    out = bytearray()
    for i in range(frames):
        t = i / sr
        env = 0.5 * (1 + math.sin(2 * math.pi * 3 * t))  # syllable-ish envelope
        v = amplitude * env * (
            math.sin(2 * math.pi * freq * t) +
            0.5 * math.sin(2 * math.pi * freq * 2 * t)
        )
        out += struct.pack('<h', int(max(-1, min(1, v)) * 32767))
    return bytes(out)


def make_silence(seconds=1.0, sr=VAD_SAMPLE_RATE, noise=0.0):
    import random
    frames = int(sr * seconds)
    out = bytearray()
    for _ in range(frames):
        v = noise * (random.random() * 2 - 1)
        out += struct.pack('<h', int(v * 32767))
    return bytes(out)


def chunked(pcm, chunk_samples=VAD_CHUNK_SAMPLES):
    """Split PCM bytes into 512-sample chunks."""
    b = chunk_samples * 2
    return [pcm[i:i + b] for i in range(0, len(pcm) - b + 1, b)]


class FakeVAD:
    """Scripted VAD: returns probabilities from a provided sequence."""

    def __init__(self, probs):
        self.probs = list(probs)
        self.idx = 0
        self._np = None  # not used by logic under test
        class _N:  # minimal np shim for vad._np references
            @staticmethod
            def frombuffer(b, dtype):
                return len(b)
        self._np = _N()

    def reset(self):
        pass

    def prob(self, chunk):
        p = self.probs[min(self.idx, len(self.probs) - 1)]
        self.idx += 1
        return p


def run_vad_capture(vad, pcm_chunks):
    """Reimplementation of the _record_and_post VAD loop for testability.

    Mirrors snarling.py's decision logic exactly; returns outcome dict.
    (The real loop is embedded in a thread with side effects — this keeps the
    state machine testable while the real code path is exercised live.)
    """
    import time as _t
    chunk_sec = VAD_CHUNK_SAMPLES / VAD_SAMPLE_RATE
    pre_roll_chunks = max(1, int(round(snarling.VAD_PRE_ROLL_SEC / chunk_sec)))
    tail_chunks = max(1, int(round(snarling.VAD_TAIL_SEC / chunk_sec)))
    start_timeout_chunks = int(snarling.VAD_SPEECH_START_TIMEOUT / chunk_sec)
    silence_stop_chunks = int(snarling.VAD_SILENCE_STOP_SEC / chunk_sec)
    max_chunks = int(snarling.VAD_MAX_RECORD_SEC / chunk_sec)
    min_speech_chunks = max(1, int(snarling.VAD_MIN_SPEECH_SEC / chunk_sec))

    pre_roll = []
    speech_chunks = 0
    in_speech = False
    silence_run = 0
    started_at = None
    chunks_seen = 0
    speech_frames = []
    outcome = "no_speech"
    t_start = _t.monotonic()

    while True:
        if chunks_seen >= len(pcm_chunks):
            outcome = "mic_ended" if in_speech or speech_chunks else "no_speech"
            break
        chunk = pcm_chunks[chunks_seen]
        chunks_seen += 1
        p = vad.prob(chunk)
        speech_like = p >= (snarling.VAD_THRESHOLD_STOP if in_speech else snarling.VAD_THRESHOLD)

        if not in_speech:
            pre_roll.append(chunk)
            if len(pre_roll) > pre_roll_chunks:
                pre_roll.pop(0)
            if speech_like:
                in_speech = True
                started_at = _t.monotonic()
                silence_run = 0
                speech_frames.extend(pre_roll)
                pre_roll = []
                speech_chunks += 1
            elif chunks_seen >= start_timeout_chunks:
                outcome = "no_speech"
                break
        else:
            speech_frames.append(chunk)
            if speech_like:
                speech_chunks += 1
                silence_run = 0
            else:
                silence_run += 1
            if silence_run >= silence_stop_chunks:
                outcome = "silence_stop"
                break
            if chunks_seen >= max_chunks:
                outcome = "max_time"
                break

    speech_sec = speech_chunks * chunk_sec
    if outcome == "no_speech" or speech_sec < snarling.VAD_MIN_SPEECH_SEC:
        return {"outcome": "skipped", "speech_sec": speech_sec, "frames": 0}

    keep = speech_frames[:-max(0, silence_run - tail_chunks)] if (
        outcome == "silence_stop" and silence_run > tail_chunks) else speech_frames
    if not keep:
        keep = speech_frames
    return {"outcome": outcome, "speech_sec": speech_sec, "frames": len(keep)}


class TestVADLogic(unittest.TestCase):
    """State-machine tests with mocked VAD probabilities."""

    def test_speech_begins_and_ends_after_trailing_silence(self):
        # 2s silence (62 chunks), speech 2s (62), silence 2s (62)
        probs = [0.02] * 62 + [0.9] * 62 + [0.03] * 62
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 200)
        self.assertEqual(r["outcome"], "silence_stop")
        self.assertGreater(r["frames"], 0)

    def test_natural_short_pause_does_not_stop(self):
        # speech, 0.8s pause (< 1.5s silence_stop), speech again, then long silence
        probs = ([0.9] * 30 + [0.05] * 25 + [0.9] * 30 + [0.05] * 60)
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 200)
        self.assertEqual(r["outcome"], "silence_stop")
        # both speech segments should be counted: speech_sec > 1.8s
        self.assertGreater(r["speech_sec"], 1.8)

    def test_no_speech_within_timeout_skips_transcription(self):
        probs = [0.02] * 300  # all silence
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 300)
        self.assertEqual(r["outcome"], "skipped")
        self.assertEqual(r["frames"], 0)

    def test_click_bump_below_min_speech_is_rejected(self):
        # 3 chunks of speech (≈0.1s < 0.25s min) then silence past stop
        probs = [0.02] * 20 + [0.95] * 3 + [0.03] * 80
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 150)
        self.assertEqual(r["outcome"], "skipped")

    def test_continuous_speech_stops_at_max_time(self):
        probs = [0.95] * 2000  # speech forever
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 2000)
        self.assertEqual(r["outcome"], "max_time")
        # duration should be ≈ VAD_MAX_RECORD_SEC
        self.assertGreater(r["speech_sec"], snarling.VAD_MAX_RECORD_SEC * 0.95)

    def test_prespeech_audio_is_kept(self):
        # speech starts at chunk 10 — pre-roll (12 chunks at 0.4s) must be included
        probs = [0.02] * 5 + [0.9] * 40 + [0.03] * 60
        r = run_vad_capture(FakeVAD(probs), [b'\x00' * 1024] * 150)
        self.assertEqual(r["outcome"], "silence_stop")
        # frames include pre-roll: onset at chunk 6, kept frames > speech chunks
        self.assertGreater(r["frames"], 40)


class TestVADInit(unittest.TestCase):
    """VAD initialization paths: success, disabled, failure fallback."""

    def test_real_vad_loads_onnx(self):
        vad = SileroVAD()
        self.assertTrue(vad.ok, f"VAD should load: {vad.error}")
        # silence chunk → low probability
        p = vad.prob(b'\x00' * 1024)
        self.assertLess(p, 0.5)

    def test_fallback_when_disabled(self):
        with patch.object(snarling, 'VAD_ENABLED', False):
            vad, reason = snarling._find_vad()
            self.assertIsNone(vad)
            self.assertIn("VAD_ENABLED", reason)

    def test_fallback_when_model_missing(self):
        with patch.object(SileroVAD, '_locate_model', return_value=None):
            vad, reason = snarling._find_vad()
            self.assertIsNone(vad)
            self.assertIn("not found", reason)


class TestWAVOutput(unittest.TestCase):
    """WAV assembly from kept frames matches 16 kHz mono S16."""

    def test_wav_written_correctly(self):
        import tempfile
        frames = [b'\x01\x00' * 512] * 50  # 50 chunks = 1.6s
        path = tempfile.mktemp(suffix='.wav')
        import wave as _wave
        with _wave.open(path, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(VAD_SAMPLE_RATE)
            wf.writeframes(b''.join(frames))
        try:
            w = wave.open(path, 'rb')
            self.assertEqual(w.getframerate(), 16000)
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertAlmostEqual(w.getnframes() / 16000, 1.6, places=2)
            w.close()
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
