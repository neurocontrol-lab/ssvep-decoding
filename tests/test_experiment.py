import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ssvep_decoding.experiment import Experiment, launch, run_experiment, session_path
from ssvep_decoding.report import build_summary


# Actual subprocesses using the launcher protocol, without a display or serial hardware.
CHILD = r'''
import json, os, sys, time
from pathlib import Path
mode, root, ready = sys.argv[1:4]
root, ready = Path(root), Path(ready)
root.mkdir()
if mode == "stimulus":
    start = Path(sys.argv[4])
    ready.touch()
    deadline = time.monotonic()+3
    while not start.exists():
        if time.monotonic() > deadline: raise SystemExit(2)
        time.sleep(.01)
    assert (root.parent/"eeg.ready").exists()
    (root/"stimulus.json").write_text(json.dumps({"status":"complete", "trials":[]}))
else:
    assert (root.parent/"stim.ready").exists()
    ready.touch()
    assert os.read(0,1) == b""
    (root/"session.json").write_text(json.dumps({"stop_reason":"launcher_closed"}))
'''


class ExperimentTests(unittest.TestCase):
    def test_session_name_reflects_actual_settings_and_sanitizes_label(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = Experiment(purpose="evaluation", mode="single", target="right",
                                window_seconds=2.5, harmonics=2, run_label=" .. / New cap: A ")
            path = session_path(root, config, "20261108_104500_000000")
            self.assertEqual(path.parent, root.resolve()/"evaluation")
            self.assertEqual(path.name, "evaluation_single-right_2p5s-2h_20261108_104500_000000_new-cap-a")
        with self.assertRaisesRegex(ValueError, "Diagnostic"):
            Experiment(purpose="../invalid").prepare()

    def test_defaults_and_short_cue_validation(self):
        self.assertEqual(Experiment().prepare()[2], 144)
        self.assertEqual(Experiment(mode="single", repeats=4).prepare()[2], 72)
        self.assertEqual(Experiment(mode="rest").prepare()[2], 75)
        with self.assertRaisesRegex(ValueError, "EEG window"):
            Experiment(cue_seconds=3).prepare()

    def test_processes_handshake_and_finish_recording_on_eof(self):
        def spawn(args, log):
            value = lambda k: args[args.index(k)+1]
            mode = log.stem
            start = [value("--start-file")] if mode == "stimulus" else []
            return launch(["-c", CHILD, mode, value("--session"), value("--ready-file"), *start], log)

        with tempfile.TemporaryDirectory() as temp, \
                patch("ssvep_decoding.experiment.lead_in", return_value=.05), \
                patch("ssvep_decoding.experiment.TAIL_SECONDS", .05):
            config = Experiment(purpose="development", run_label="Protocol A")
            folder, text = run_experiment(config, Path(temp)/"sessions", threading.Event(), spawn=spawn)
            output = Path(temp)/"results"/folder.name/"live"
            summary = json.loads((output/"summary.json").read_text())
            self.assertEqual(summary["status"], "complete", text)
            self.assertEqual(summary["decoder_stop"], "launcher_closed")
            self.assertEqual(summary["purpose"], "development")
            self.assertEqual(summary["run_label"], "Protocol A")
            self.assertTrue((output/"summary.txt").is_file())
            montage = json.loads((folder/"montage.json").read_text())
            self.assertEqual(montage["channels"], {"D1": "O1", "D2": "Oz", "D3": "O2", "D4": "POz"})
            self.assertEqual(summary["montage"], montage)

    def test_cancel_before_start_does_not_open_hardware(self):
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as temp:
            def spawn(*_):
                self.fail("Cancelled experiment launched a process")
            folder, _ = run_experiment(Experiment(montage=""), Path(temp)/"sessions", cancelled, spawn=spawn)
            output = Path(temp)/"results"/folder.name/"live"
            summary = json.loads((output/"summary.json").read_text())
            self.assertEqual(summary["status"], "cancelled")
            self.assertIsNone(summary["montage"])

    def test_report_separates_cue_rest_and_transitions(self):
        def event(t, direction, emit):
            return {"valid": True, "host_perf_counter": t, "scores": {"right": .6 if direction == "right" else .2,
                    "left": .6 if direction == "left" else .2}, "score": .6, "emit": emit, "accepted": direction}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"decoder").mkdir()
            (root/"stimulus").mkdir()
            (root/"decoder_profile.json").write_text('{"window_seconds":4}')
            stimulus = {"trials": [{"cue":"right","host_perf_counter_before_flip":99.99,"host_perf_counter_after_flip":100},
                                   {"cue":None,"host_perf_counter_before_flip":111.99,"host_perf_counter_after_flip":112}],
                        "host_perf_counter_end":118, "status":"complete"}
            (root/"stimulus/stimulus.json").write_text(json.dumps(stimulus))
            rows = [event(101,"right",True), event(105,"right",True), event(106,"left",True),
                    event(111,"right",False), event(117,"left",True), event(130,"right",True)]
            (root/"decoder/events.jsonl").write_text("\n".join(map(json.dumps,rows)))
            report = build_summary(root, "single", "complete")
            target = report["targets"]["right"]
            self.assertEqual((target["ranked_first"], target["windows"]), (2,3))
            self.assertEqual((target["correct_emissions"], target["wrong_emissions"]), (1,1))
            self.assertEqual(report["rest"]["emissions"], 1)
            self.assertEqual(report["unlabeled_emissions"], 2)
            idle = build_summary(root, "rest", "complete")
            self.assertEqual(idle["rest"]["emissions"], 5)


if __name__ == "__main__":
    unittest.main()
