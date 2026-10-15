import argparse
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

from ssvep_decoding.cca import CCA
from ssvep_decoding.cli import run
from ssvep_decoding.config import read_profile, reference_frequency, validate
from ssvep_decoding.decision import Decision
from ssvep_decoding.pipeline import Pipeline
from ssvep_decoding.output import Keyboard
from ssvep_decoding.sources import Frame, Replay, SerialLines, SerialSource, Synthetic, check_metadata, parse_row, U32
from ssvep_decoding.stimulus import bright_at, trial_plan


def profiles():
    return tuple(read_profile(kind) for kind in ("acquisition", "stimulus", "decoder", "control"))


def synthetic(target="forward", seconds=7):
    a, s, d, c = profiles()
    source = Synthetic(a, {t["id"]: reference_frequency(s, t) for t in s["targets"]}, target, seconds)
    return source, Pipeline(a, s, d, c, source.metadata)


class CCATests(unittest.TestCase):
    def test_every_target_under_noise_and_phase_shifts(self):
        a, s, d, _ = profiles()
        fs = a["sample_rate_hz"]
        frequencies = {t["id"]: reference_frequency(s, t) for t in s["targets"]}
        cca = CCA(fs, 500, frequencies, 3)
        time = np.arange(500)/fs
        for target, freq in frequencies.items():
            for seed in range(3):
                rng = np.random.default_rng(seed)
                signal = np.sin(2*np.pi*freq*time[:, None] + np.arange(4)[None, :])
                x = 2048+50*signal+rng.normal(0, 20, (500, 4))
                scores = cca.scores(x)
                self.assertEqual(max(scores, key=scores.get), target)

    def test_rank_deficient_and_flat_channels(self):
        t = np.arange(500)/250
        x = np.column_stack([np.sin(2*np.pi*10*t)]*3+[np.ones(500)])
        cca = CCA(250, 500, {"target": 10}, 3)
        self.assertGreater(cca.scores(x)["target"], 0.999)
        self.assertEqual(cca.scores(np.ones((500, 4)))["target"], 0)

    def test_shared_harmonic_is_ambiguous(self):
        t = np.arange(500)/250
        scores = CCA(250, 500, {"ten": 10, "fifteen": 15}, 3).scores(np.sin(2*np.pi*30*t)[:, None])
        decision = Decision(read_profile("decoder")).update(scores, 0)
        self.assertIsNone(decision["candidate"])


class DecisionTests(unittest.TestCase):
    def test_persistence_repeat_and_ambiguity_reset(self):
        decision = Decision(read_profile("decoder"))
        scores = {"left": 0.9, "right": 0.2}
        for t in (0, 0.25, 0.5):
            self.assertFalse(decision.update(scores, t)["emit"])
        self.assertTrue(decision.update(scores, 0.75)["emit"])
        self.assertFalse(decision.update(scores, 1)["emit"])
        self.assertIsNone(decision.update({"left": 0.8, "right": 0.79}, 1.25)["accepted"])
        self.assertIsNone(decision.update(scores, 1.5)["accepted"])
        decision.update(scores, 1.75); decision.update(scores, 2)
        self.assertTrue(decision.update(scores, 2.25)["emit"])

    def test_long_gap_does_not_count_as_evidence(self):
        decision = Decision(read_profile("decoder"))
        scores = {"left": 0.9, "right": 0.1}
        decision.update(scores, 0)
        self.assertIsNone(decision.update(scores, 10)["accepted"])


class PipelineTests(unittest.TestCase):
    def test_all_targets_emit_correct_actions(self):
        for target, action in (("forward", "forward"), ("backward", "backward"), ("left", "turn_left"), ("right", "turn_right")):
            source, pipeline = synthetic(target)
            emissions = []
            for frame in source:
                result = pipeline.feed(frame)
                if result and result["emit"]:
                    emissions.append(result)
            self.assertTrue(emissions, target)
            self.assertTrue(all(e["accepted"] == target and e["action"] == action for e in emissions))
            self.assertGreater(emissions[0]["time_seconds"], 3.5)

    def test_noise_rest_does_not_emit(self):
        source, pipeline = synthetic("rest", 12)
        self.assertFalse(any(result and result["emit"] for result in map(pipeline.feed, source)))

    def test_lost_sample_clears_window_and_evidence(self):
        source, pipeline = synthetic()
        frames = list(source)
        for frame in frames[:1100]:
            pipeline.feed(frame)
        result = pipeline.feed(frames[1101])
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "missing_samples")
        self.assertEqual(len(pipeline.raw), 0)
        self.assertIsNone(pipeline.decision.candidate)
        self.assertIsNone(pipeline.feed(frames[1102]))

    def test_jitter_and_scan_overrun(self):
        source, pipeline = synthetic()
        frames = list(source)
        pipeline.feed(frames[0])
        row = list(frames[1].row); row[1] += 1500
        self.assertEqual(pipeline.feed(Frame(tuple(row)))["reason"], "timing_jitter")
        _, pipeline = synthetic()
        row = list(frames[0].row); row[2] = 4000
        self.assertEqual(pipeline.feed(Frame(tuple(row)))["reason"], "scan_overrun")

    def test_timestamp_wrap(self):
        source, pipeline = synthetic()
        for frame in source:
            row = list(frame.row); row[1] = (row[1]+U32-8000) % U32
            pipeline.feed(Frame(tuple(row)))
        self.assertGreater(pipeline.elapsed, 6.9)

    def test_flat_clipped_and_reset(self):
        _, pipeline = synthetic()
        for i in range(800):
            result = pipeline.feed(Frame((i, i*4000, 600, 0, 0, 0, 2048, 2048, 2048, 2048)))
            if result:
                self.assertEqual(result["reason"], "flat_channel")
        with self.assertRaises(ValueError):
            pipeline.feed(Frame((0, 0, 600, 0, 0, 0, 2048, 2048, 2048, 2048)))


class ConfigAndProtocolTests(unittest.TestCase):
    def test_profiles_and_independent_mapping(self):
        a, s, d, c = profiles()
        validate(a, s, d, c)
        c["action_keys"]["turn_left"] = "a"
        s["targets"][0]["reference_hz"] = 8.55
        validate(a, s, d, c)
        self.assertEqual(reference_frequency(s, s["targets"][0]), 8.55)

    def test_unsupported_harmonics_and_duplicate_frequencies(self):
        a, s, d, c = profiles()
        d["bandpass_hz"][1] = 40
        with self.assertRaises(ValueError): validate(a, s, d, c)
        a, s, d, c = profiles()
        s["targets"][0]["period_frames"] = 6
        with self.assertRaises(ValueError): validate(a, s, d, c)

    def test_metadata_mismatch_and_invalid_adc(self):
        source, _ = synthetic()
        meta = copy.deepcopy(source.metadata); meta["sample_rate_hz"] = 200
        with self.assertRaises(ValueError): check_metadata(meta, read_profile("acquisition"))
        with self.assertRaises(ValueError): parse_row("0,0,0,0,0,0,4096,0,0,0")

    def test_frame_sequences_and_repeatable_cues(self):
        target = read_profile("stimulus")["targets"][0]
        self.assertEqual([bright_at(i, target) for i in range(7)], [True]*3+[False]*4)
        plan = trial_plan(["forward", "left"], 2, 6, 3, 60, 42)
        self.assertEqual(plan, trial_plan(["forward", "left"], 2, 6, 3, 60, 42))
        self.assertEqual(sum(count for _, count in plan), 4*9*60)

    def test_serial_fragmentation_and_timeout(self):
        class Port:
            in_waiting = 1
            data = bytearray(b"a\r\nb\n")
            def read(self, count):
                result = self.data[:count]; del self.data[:count]; return result
        reader = SerialLines(Port())
        self.assertEqual(reader.line(), "a")
        self.assertEqual(reader.line(), "b")
        with self.assertRaises(TimeoutError): reader.line(timeout=0.001)


class SessionTests(unittest.TestCase):
    def test_synthetic_record_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            base = dict(acquisition=None, stimulus=None, decoder=None, control=None,
                        keyboard=False, window_title=None, seconds=6, seed=42, target="left")
            args = argparse.Namespace(**base, mode="synthetic", session=Path(temp)/"synth")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run(args), 0)
                replay = argparse.Namespace(**base, mode="replay", input=args.session/"raw.csv", session=Path(temp)/"replay")
                self.assertEqual(run(replay), 0)
            events = [json.loads(line) for line in (replay.session/"events.jsonl").read_text().splitlines()]
            self.assertTrue(any(e["emit"] and e["accepted"] == "left" for e in events))
            session = json.loads((replay.session/"session.json").read_text())
            self.assertEqual(session["frames_received"], 1500)
            self.assertFalse(session["keyboard_enabled"])

    def test_keyboard_cannot_be_armed_for_synthetic(self):
        args = argparse.Namespace(mode="synthetic", acquisition=None, stimulus=None,
                                  decoder=None, control=None, keyboard=True, window_title="test")
        with self.assertRaises(ValueError): run(args)


class OutputTests(unittest.TestCase):
    def test_focus_guard_and_invalid_decision_release_without_os_injection(self):
        keyboard = Keyboard.__new__(Keyboard)
        keyboard.key, keyboard.error = None, None
        keyboard.lock = threading.Lock()
        keyboard.pulse = 0.12
        sent = []
        keyboard.send = lambda key, up: sent.append((key, up))
        keyboard.focused = lambda: False
        result = {"accepted": "forward", "emit": True, "key": "w"}
        keyboard.update(result)
        self.assertEqual(sent, [])
        keyboard.focused = lambda: True
        keyboard.update(result)
        self.assertEqual(sent, [("w", False)])
        keyboard.update({"accepted": None})
        self.assertEqual(sent[-1], ("w", True))
        self.assertIsNone(keyboard.key)

    def test_watchdog_expiration_releases_without_another_decision(self):
        keyboard = Keyboard.__new__(Keyboard)
        keyboard.key, keyboard.until, keyboard.error = "w", 0, None
        keyboard.lock = threading.Lock()
        keyboard.stop = threading.Event()
        keyboard.focused = lambda: True
        sent = []
        def send(key, up):
            sent.append((key, up))
            keyboard.stop.set()
        keyboard.send = send
        keyboard.watch()
        self.assertEqual(sent, [("w", True)])


class LiveContractTests(unittest.TestCase):
    def test_live_handshake_row_and_stop(self):
        import types
        source, _ = synthetic()
        metadata = source.metadata
        class Port:
            def __init__(self):
                self.buffer, self.commands, self.is_open = bytearray(), [], True
            @property
            def in_waiting(self): return len(self.buffer)
            def read(self, count):
                result = self.buffer[:count]; del self.buffer[:count]; return result
            def reset_input_buffer(self): self.buffer.clear()
            def write(self, command):
                self.commands.append(command)
                if command == b"s\n": self.buffer.extend(b"#end {}\n")
                else:
                    text = "#meta "+json.dumps(metadata)+"\n"
                    text += "sequence,timestamp_us,scan_us,late_us,missed_deadlines,queue_overflows,"+",".join(metadata["channels"])+"\n"
                    text += "0,0,600,0,0,0,2000,2010,2020,2030\n"
                    self.buffer.extend(text.encode())
            def close(self): self.is_open = False
        port = Port()
        with patch.dict("sys.modules", {"serial": types.SimpleNamespace(Serial=lambda *a, **k: port)}), \
                patch("ssvep_decoding.sources.time.sleep"):
            live = SerialSource("COM_TEST", read_profile("acquisition"))
            self.assertEqual(next(iter(live)).row[6:], (2000, 2010, 2020, 2030))
            live.origin_host -= 2
            port.buffer.extend(b"1,4000,600,0,0,0,2000,2010,2020,2030\n")
            with self.assertRaises(TimeoutError):
                next(iter(live))
            live.close()
        self.assertEqual(port.commands, [b"s\n", b"r\n", b"s\n"])
        self.assertFalse(port.is_open)


if __name__ == "__main__":
    unittest.main()
