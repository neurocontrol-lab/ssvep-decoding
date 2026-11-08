"""Prepare and supervise one log-only PsychoPy/EEG experiment."""
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time

from .config import read_profile, validate
from .report import build_summary, render_summary
from .stimulus import trial_plan


PRESETS = {"4 s / 3 harmonics": (4, 3), "2 s / 2 harmonics": (2, 2),
           "2 s / 3 harmonics": (2, 3), "3 s / 3 harmonics": (3, 3)}
TAIL_SECONDS = 5
PURPOSES = {
    "diagnostic": "Check contacts, timing, or integration.",
    "development": "Compare settings or tune the decoder.",
    "evaluation": "Fresh data for a protocol and decoder fixed before the run.",
}


@dataclass(frozen=True)
class Experiment:
    purpose: str = "diagnostic"
    run_label: str = ""
    mode: str = "four"
    target: str = "forward"
    port: str = "COM3"
    repeats: int = 2
    cue_seconds: float = 12
    rest_seconds: float = 6
    idle_seconds: float = 75
    window_seconds: float = 4
    harmonics: int = 3
    min_score: float = 0.55
    min_margin: float = 0.10
    persistence_seconds: float = 0.75
    screen: int = 0
    windowed: bool = False
    width: int = 1280
    height: int = 720
    seed: int = 43
    stimulus_path: str = ""

    def prepare(self):
        if self.purpose not in PURPOSES:
            raise ValueError("Choose Diagnostic, Development, or Evaluation.")
        if self.mode not in ("single", "four", "rest") or not self.port.strip():
            raise ValueError("Choose a test and enter the serial port.")
        stimulus = read_profile("stimulus", self.stimulus_path or None)
        decoder = read_profile("decoder")
        for key in ("window_seconds", "harmonics", "min_score", "min_margin", "persistence_seconds"):
            decoder[key] = getattr(self, key)
        validate(read_profile("acquisition"), stimulus, decoder, read_profile("control"))
        if self.mode == "rest":
            if not decoder["warmup_seconds"] + self.window_seconds < self.idle_seconds <= 3600:
                raise ValueError("Rest duration must exceed warmup plus the EEG window, and be at most 3600 seconds.")
            return stimulus, decoder, self.idle_seconds
        if not 1 <= self.repeats <= 100 or not 0 < self.cue_seconds <= 60 or not 0 < self.rest_seconds <= 60:
            raise ValueError("Use 1–100 repeats and cue/rest durations greater than 0 and at most 60 seconds.")
        if self.cue_seconds <= self.window_seconds + .4:
            raise ValueError("Allow more than an EEG window plus 0.4 seconds per cue for scoring.")
        if self.screen < 0 or not 320 <= self.width <= 16384 or not 240 <= self.height <= 16384:
            raise ValueError("Use a nonnegative screen index and a valid window size (320 × 240 or larger).")
        targets = [t["id"] for t in stimulus["targets"]]
        if self.mode == "single":
            if self.target not in targets:
                raise ValueError("Single target must be present in the stimulus profile.")
            targets = [self.target]
        refresh = stimulus["refresh_hz"]
        plan = trial_plan(targets, self.repeats, self.cue_seconds, self.rest_seconds, refresh, self.seed)
        if any(count <= 0 for _, count in plan):
            raise ValueError("Cue and rest intervals must each span at least one display frame.")
        duration = sum(count for _, count in plan) / refresh
        if duration + lead_in(decoder) + 35 > 3600:
            raise ValueError("The experiment plus capture padding must fit within one hour.")
        return stimulus, decoder, duration


def lead_in(decoder):
    return max(5.0, decoder["warmup_seconds"] + decoder["window_seconds"] + .3)


def session_path(sessions, config, stamp):
    def slug(text):
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].rstrip("-")

    mode = {"single": f"single-{slug(config.target)}", "four": "four-target", "rest": "rest"}[config.mode]
    window = f"{config.window_seconds:g}".replace(".", "p")
    parts = [config.purpose, mode, f"{window}s-{config.harmonics}h", stamp]
    if slug(config.run_label):
        parts.append(slug(config.run_label))
    return sessions.resolve() / config.purpose / "_".join(parts)


class Cancelled(Exception):
    pass


def launch(args, log):
    with log.open("wb") as file:
        return subprocess.Popen(
            [sys.executable, "-u", *args], stdin=subprocess.PIPE, stdout=file, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def stop_children(children):
    for process in children.values():
        if process.stdin:
            process.stdin.close()
    deadline = time.monotonic() + 8
    forced = []
    for name, process in children.items():
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            forced.append(name)
            process.kill()
            process.wait(timeout=2)
    return forced


def run_experiment(config, sessions, cancel, notify=lambda _: None, spawn=launch):
    stimulus, decoder, duration = config.prepare()
    folder = session_path(sessions, config, datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "settings.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
    stimulus_path, decoder_path = folder / "stimulus_profile.json", folder / "decoder_profile.json"
    for path, profile in ((stimulus_path, stimulus), (decoder_path, decoder)):
        path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    profiles = ["--stimulus", str(stimulus_path), "--decoder", str(decoder_path)]
    children = {}
    status, problem = "complete", ""
    eeg_ready = folder / "eeg.ready"
    stim_ready, start = folder / "stim.ready", folder / "stim.start"

    def start_child(name, args):
        children[name] = spawn(args, folder / (name + ".log"))

    def check_exit(name):
        child = children[name]
        code = child.poll()
        if code is not None:
            if name == "stimulus":
                path = folder / name / "stimulus.json"
                if path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("status") in ("cancelled", "interrupted"):
                    raise Cancelled("Stimulus stopped with Escape.")
            raise RuntimeError(f"{name.capitalize()} exited early (code {code}). See {name}.log.")

    def wait_for(condition, seconds, watch, message):
        deadline = time.monotonic() + seconds
        while True:
            if cancel.is_set():
                raise Cancelled("Stopped by operator.")
            if condition():
                return
            for name in watch:
                check_exit(name)
            if time.monotonic() >= deadline:
                raise TimeoutError(message)
            cancel.wait(.05)

    try:
        if cancel.is_set():
            raise Cancelled("Stopped by operator.")
        if config.mode != "rest":
            notify("Preparing PsychoPy display…")
            args = ["-m", "ssvep_decoding.stimulus", *profiles,
                    "--repeats", str(config.repeats), "--cue-seconds", str(config.cue_seconds),
                    "--rest-seconds", str(config.rest_seconds), "--screen", str(config.screen),
                    "--size", str(config.width), str(config.height), "--seed", str(config.seed),
                    "--session", str(folder / "stimulus"), "--ready-file", str(stim_ready),
                    "--start-file", str(start), "--stop-on-stdin"]
            if config.mode == "single":
                args += ["--single", config.target]
            if config.windowed:
                args += ["--windowed"]
            start_child("stimulus", args)
            wait_for(stim_ready.exists, 45, ("stimulus",), "PsychoPy did not become ready. See stimulus.log.")
        notify(f"Opening {config.port} and waiting for EEG…")
        budget = duration if config.mode == "rest" else math.ceil(duration + lead_in(decoder) + 35)
        start_child("decoder", ["-m", "ssvep_decoding", "live", *profiles, "--port", config.port.strip(),
                                "--seconds", str(budget), "--session", str(folder / "decoder"),
                                "--ready-file", str(eeg_ready), "--stop-on-stdin"])
        wait_for(eeg_ready.exists, 25, tuple(children), "No EEG samples arrived. See decoder.log.")
        if config.mode == "rest":
            notify(f"Recording {duration:g} s of rest with flicker off…")
            wait_for(lambda: children["decoder"].poll() is not None, duration + 10, (),
                     "Rest recording did not finish. See decoder.log.")
            if children["decoder"].returncode != 0:
                raise RuntimeError("Rest recording failed. See decoder.log.")
            result = json.loads((folder / "decoder/session.json").read_text(encoding="utf-8"))
            if result.get("stop_reason") != "requested_duration":
                raise RuntimeError("Rest recording ended before its requested duration. See decoder.log.")
        else:
            notify("EEG connected; collecting the first decoding window…")
            onset = time.monotonic() + lead_in(decoder)
            wait_for(lambda: time.monotonic() >= onset, lead_in(decoder) + 1, tuple(children), "EEG warmup timed out.")
            start.touch(exist_ok=False)
            notify(f"Stimulus running (about {duration:g} s). Escape stops the test.")
            wait_for(lambda: children["stimulus"].poll() is not None, duration + 20, ("decoder",),
                     "The stimulus ran longer than expected. See stimulus.log.")
            result = json.loads((folder / "stimulus/stimulus.json").read_text(encoding="utf-8"))
            if result.get("status") in ("cancelled", "interrupted"):
                raise Cancelled("Stimulus stopped with Escape.")
            if children["stimulus"].returncode != 0 or result.get("status") != "complete":
                raise RuntimeError("Stimulus failed. See stimulus.log.")
            notify(f"Stimulus complete; recording {TAIL_SECONDS:g} s of trailing rest…")
            end = time.monotonic() + TAIL_SECONDS
            wait_for(lambda: time.monotonic() >= end, TAIL_SECONDS + 1, ("decoder",), "Recording did not finish.")
    except Cancelled as error:
        status, problem = "cancelled", str(error)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        status, problem = "error", str(error)
    finally:
        forced = stop_children(children)
    if forced:
        status, problem = "error", f"{problem} Forced stop: {', '.join(forced)}. Partial files may remain.".strip()
    elif status == "complete" and children["decoder"].returncode != 0:
        status, problem = "error", "Decoder failed before clean shutdown. See decoder.log."
    summary = build_summary(folder, config.mode, status, problem)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    text = render_summary(summary)
    (folder / "summary.txt").write_text(text + "\n", encoding="utf-8")
    return folder, text
