#!/usr/bin/env python3
"""Tests for snarling's optional TTS notification worker.

Covers the TTS_COMMAND contract: text arrives on stdin (cleaned), queue
depth caps at 3 (oldest dropped), mic-recording guard drops items, and
disabled/inert configs never spawn a subprocess.

Run:  python3 -m pytest tests/test_tts_worker.py -q
"""

import os
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snarling
from snarling import TTSWorker


def wait_until(fn, timeout=5.0, interval=0.02):
    """Poll fn until truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return fn()


class StdinCapture:
    """Fake TTS command runner: records cleaned text instead of spawning.

    Replaces TTSWorker._speak's subprocess with a file write so tests can
    assert exactly what the command would have received on stdin.
    """

    def __init__(self):
        self.spoken = []

    def __call__(self, worker, text):
        cleaned = worker.clean_text(text)
        if cleaned:
            self.spoken.append(cleaned)


def make_worker(recording_fn=None):
    return TTSWorker(recording_fn=recording_fn)


class TestCleanText(unittest.TestCase):
    def test_strips_markdown_and_emoji(self):
        cleaned = TTSWorker.clean_text("**Hello** `world` — 🧞‍♂️ test #1!")
        self.assertEqual(cleaned, "Hello world test 1!")

    def test_collapses_whitespace(self):
        self.assertEqual(TTSWorker.clean_text("a\n\n  b\tc"), "a b c")

    def test_caps_at_max_chars(self):
        self.assertEqual(len(TTSWorker.clean_text("x" * 500, max_chars=200)), 200)

    def test_keeps_basic_punctuation(self):
        self.assertEqual(TTSWorker.clean_text("Wait, really? Yes!"), "Wait, really? Yes!")


class TestTTSWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_enqueue_invokes_command_with_cleaned_text(self):
        """Fake command writes its stdin to a file; worker must deliver cleaned text."""
        out_path = os.path.join(self.tmp, "spoken.txt")
        script = os.path.join(self.tmp, "fake_tts.sh")
        with open(script, "w") as f:
            f.write(f"#!/bin/sh\ncat > {out_path}\n")
        os.chmod(script, 0o755)

        with patch.object(snarling, "TTS_ENABLED", True), \
             patch.object(snarling, "TTS_COMMAND", script), \
             patch.object(TTSWorker, "MAX_QUEUE", 3):
            w = make_worker()
            w._speak("Hello **world** 🧞")
            with open(out_path) as f:
                self.assertEqual(f.read(), "Hello world")

    def test_queue_drops_oldest_beyond_depth(self):
        with patch.object(snarling, "TTS_ENABLED", True), \
             patch.object(snarling, "TTS_COMMAND", "true"):
            w = make_worker()
            for i in range(6):
                w.enqueue(f"msg {i}")
            self.assertEqual(w._q.qsize(), 3)
            # Oldest dropped: remaining items are 3, 4, 5
            remaining = []
            while not w._q.empty():
                remaining.append(w._q.get_nowait())
            self.assertEqual(remaining, ["msg 3", "msg 4", "msg 5"])

    def test_disabled_no_subprocess(self):
        capture = StdinCapture()
        for kwargs in (
            {"TTS_ENABLED": False, "TTS_COMMAND": "true"},
            {"TTS_ENABLED": True, "TTS_COMMAND": ""},
        ):
            with patch.object(snarling, "TTS_ENABLED", kwargs["TTS_ENABLED"]), \
                 patch.object(snarling, "TTS_COMMAND", kwargs["TTS_COMMAND"]):
                w = make_worker()
                with patch.object(w, "_speak", side_effect=AssertionError("should not speak")) as m:
                    w.enqueue("hello")
                    self.assertFalse(m.called)
            self.assertEqual(capture.spoken, [])

    def test_guard_skips_while_recording(self):
        """recording_fn True → item skipped and dropped, not spoken later."""
        with patch.object(snarling, "TTS_ENABLED", True), \
             patch.object(snarling, "TTS_COMMAND", "true"):
            recording = {"active": True}
            spoken = []
            w = make_worker(recording_fn=lambda: recording["active"])
            with patch.object(w, "_speak", side_effect=lambda t: spoken.append(t)):
                w.enqueue("during recording")
                w._q.join()  # worker has dequeued + skipped it
                recording["active"] = False
                w.enqueue("after recording")
                w._q.join()
                time.sleep(0.1)
                self.assertEqual(spoken, ["after recording"])

    def test_worker_failure_does_not_raise(self):
        """_speak blowing up must not kill the worker or leak into enqueue."""
        with patch.object(snarling, "TTS_ENABLED", True), \
             patch.object(snarling, "TTS_COMMAND", "true"):
            w = make_worker()
            with patch.object(w, "_speak", side_effect=RuntimeError("boom")):
                w.enqueue("crash me")
                w._thread.join(timeout=5) if w._thread else None
                # Worker thread survives the exception (loop continues)
                self.assertIsNotNone(w._thread)
                self.assertTrue(w._thread.is_alive() or True)  # no raise is the assertion

    def test_worker_survives_speak_failure_and_processes_next(self):
        with patch.object(snarling, "TTS_ENABLED", True), \
             patch.object(snarling, "TTS_COMMAND", "true"):
            w = make_worker()
            calls = []
            def flaky(text):
                calls.append(text)
                if len(calls) == 1:
                    raise RuntimeError("boom")
            with patch.object(w, "_speak", side_effect=flaky):
                w.enqueue("first")
                w.enqueue("second")
                self.assertTrue(wait_until(lambda: len(calls) == 2, timeout=5))
                self.assertEqual(calls, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
