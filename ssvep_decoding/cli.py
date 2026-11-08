import argparse
import csv
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import os
import re
import sys
import threading
import time

from . import __version__
from .config import read_profile, reference_frequency, validate
from .output import Keyboard, KEYS
from .maze_output import MazeOutput
from .montage import read_montage
from .pipeline import Pipeline
from .report import build_summary, read_json, save_summary
from .sources import FIELDS, Replay, SerialSource, Synthetic


def profile_arguments(parser):
    for kind in ("acquisition", "stimulus", "decoder", "control"):
        parser.add_argument("--"+kind, type=Path, help=f"Override {kind} JSON profile")


def load_profiles(args):
    profiles = {kind: read_profile(kind, getattr(args, kind, None))
                for kind in ("acquisition", "stimulus", "decoder", "control")}
    validate(*(profiles[k] for k in ("acquisition", "stimulus", "decoder", "control")))
    return profiles


def launcher_stop_signal(enabled):
    stopped = threading.Event()
    if enabled:
        def watch():
            try:
                # Use the raw descriptor so shutdown cannot wait on a
                # buffered stdin lock held by this daemon thread.
                os.read(sys.stdin.fileno(), 1)
            except (OSError, ValueError, AttributeError):
                pass
            finally:
                stopped.set()
        threading.Thread(target=watch, daemon=True).start()
    return stopped


def run(args):
    profiles = load_profiles(args)
    a, s, d, c = (profiles[k] for k in ("acquisition", "stimulus", "decoder", "control"))
    if args.keyboard and (args.mode != "live" or not args.window_title):
        raise ValueError("Keyboard output requires live mode and --window-title.")
    if args.keyboard and any(key not in KEYS for key in c["action_keys"].values()):
        raise ValueError("Keyboard keys must be w/a/s/d or left/right/up/down.")
    maze_control = getattr(args, "maze_control", False)
    maze_hud = getattr(args, "maze_hud", False) or maze_control
    if maze_hud and args.mode != "live":
        raise ValueError("Maze output requires live EEG.")
    if maze_hud and args.keyboard:
        raise ValueError("Choose maze output or keyboard output for this run.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    results = Path("results").resolve()
    replay = args.mode == "replay"
    stimulus_path = getattr(args, "stimulus_log", None)
    report_options = {}
    report_mode = args.mode
    lineage = {}
    if replay:
        recording = args.input.resolve()
        original_dir = recording.parent
        original_session = original_dir.parent if original_dir.name == "decoder" else original_dir
        original_metadata = read_json(original_dir / "session.json", {}) if recording.name == "raw.csv" else {}
        if stimulus_path and not original_metadata:
            raise ValueError("--stimulus-log requires the original decoder recording and session.json.")
        if recording.name == "raw.csv":
            montage = original_metadata.get("montage") or read_json(original_session / "montage.json", None)
        else:
            montage = None
        source_id = original_session.name if recording.name == "raw.csv" else recording.stem
        label = re.sub(r"[^a-z0-9]+", "-", getattr(args, "label", "").lower()).strip("-")[:48]
        setting = f"{d['window_seconds']:g}s-{d['harmonics']}h".replace(".", "p")
        replay_id = "_".join(filter(None, (setting, stamp, label)))
        directory = args.session or results / source_id / "replays" / replay_id
        output = directory
        report_folder = original_session
        settings = read_json(original_session / "settings.json", {}) if original_metadata else {}
        report_mode = settings.get("mode", "replay")
        report_options.update(raw_path=recording, stimulus_path=stimulus_path, original_dir=original_dir)
        lineage = {"source_recording": str(recording), "source_session": str(original_session),
                   "replay_label": getattr(args, "label", "")}
    else:
        montage = read_montage(getattr(args, "montage", None), a["channels"])
        directory = args.session or Path("sessions") / stamp
        report_folder = directory
        output = results / directory.resolve().name / "live"
        lineage = {"source_session": str(directory.resolve())}
    directory.mkdir(parents=True, exist_ok=False)
    launcher_stopped = launcher_stop_signal(getattr(args, "stop_on_stdin", False))
    ready_file = getattr(args, "ready_file", None)
    source = keyboard = maze = None
    reason, received, updates, commands = "complete", 0, 0, 0
    started = time.perf_counter()
    summary = {"version": __version__, "mode": args.mode, "profiles": profiles,
               "keyboard_enabled": args.keyboard, "window_title": args.window_title,
               "host_start_perf_counter": started,
               "started_at": datetime.now().astimezone().isoformat(),
               "montage": montage,
               "maze_hud_enabled": maze_hud, "maze_control_requested": maze_control}
    summary.update(lineage)
    try:
        if args.mode == "synthetic":
            source = Synthetic(a, {t["id"]: reference_frequency(s, t) for t in s["targets"]},
                               args.target, args.seconds, args.seed)
        elif args.mode == "replay":
            source = Replay(args.input, a)
        else:
            source = SerialSource(args.port, a)
        summary["metadata"] = source.metadata
        if args.keyboard:
            keyboard = Keyboard(args.window_title, c["key_pulse_seconds"])
        if maze_hud:
            maze = MazeOutput(s, c, steering=maze_control)
            maze.warm()
        pipeline = Pipeline(a, s, d, c, source.metadata)
        summary["effective_window_samples"] = pipeline.n
        summary["effective_hop_samples"] = pipeline.hop
        summary["effective_update_seconds"] = pipeline.hop/pipeline.fs
        print(f"Session: {directory.resolve()}\nOutput: {'KEYBOARD ARMED' if keyboard else 'display/log only'}")
        if maze:
            print("Maze link: localhost UDP 46081" + (" (auto-start in game; F3 pauses/resumes)" if maze_control else " (monitor only)"))
        with ExitStack() as files:
            events = files.enter_context((directory / "events.jsonl").open("x", encoding="utf-8"))
            if not replay:
                raw = files.enter_context((directory / "raw.csv").open("x", newline="", encoding="utf-8"))
                writer = csv.writer(raw)
                writer.writerow(FIELDS+source.metadata["channels"])
            live_start = time.perf_counter()
            for frame in source:
                if launcher_stopped.is_set():
                    reason = "launcher_closed"
                    break
                received += 1
                if not replay:
                    writer.writerow(frame.row)
                if received == 1 and ready_file is not None:
                    ready_file.touch(exist_ok=False)
                result = pipeline.feed(frame)
                if maze:
                    if result is not None:
                        maze.update(result)
                    elif updates == 0:
                        maze.warm()
                if result is not None:
                    updates += 1
                    result["sequence"] = frame.row[0]
                    result["host_perf_counter"] = time.perf_counter()
                    if keyboard:
                        keyboard.update(result)
                    result["output_mode"] = ("keyboard" if keyboard else "maze_control" if maze_control
                                             else "maze_hud" if maze_hud else "log")
                    events.write(json.dumps(result)+"\n")
                    events.flush()
                    if result["emit"]:
                        commands += 1
                        print(f"{result['time_seconds']:7.2f}s {result['accepted']:10} -> {result['action']:10} [{result['key']}] score={result['score']:.3f} margin={result['margin']:.3f}")
                    elif not result["valid"]:
                        print(f"{result['time_seconds']:7.2f}s no command: {result['reason']}")
                if not replay and received % 250 == 0:
                    raw.flush()
                if args.mode == "live" and time.perf_counter()-live_start >= args.seconds:
                    reason = "requested_duration"
                    break
            summary["elapsed_eeg_seconds"] = pipeline.elapsed
            summary["input_end"] = source.end
    except BaseException as error:
        reason = "interrupted" if isinstance(error, KeyboardInterrupt) else f"error: {error}"
        raise
    finally:
        try:
            if maze:
                maze.close()
            if keyboard:
                keyboard.close()
        finally:
            try:
                if source:
                    source.close()
            finally:
                summary.update(stop_reason=reason, frames_received=received,
                               decision_updates=updates, command_events=commands)
                (directory / "session.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                if source and not replay:
                    # Only propagate #end if the source was consumed through EOF.
                    (directory / "raw.json").write_text(json.dumps({"metadata": source.metadata,
                        "end": source.end if reason == "complete" else None}, indent=2), encoding="utf-8")
                if replay or (args.mode == "live" and not getattr(args, "defer_summary", False)):
                    problem = reason if reason.startswith("error:") else ""
                    status = "error" if problem else "interrupted" if reason == "interrupted" else "complete"
                    try:
                        report = build_summary(report_folder, report_mode, status, problem,
                                               decoder_dir=directory, **report_options)
                        report.update(montage=montage, **lineage)
                        save_summary(output, report)
                        print(f"Results: {output.resolve()}")
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        if status == "complete":
                            raise
                        print(f"Could not summarize partial session: {error}", file=sys.stderr)
    print(f"Saved {received} frames, {updates} decisions, {commands} command events.")
    if updates == 0:
        print("No complete eligible window: check capture duration and timing/quality diagnostics.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="SSVEP CCA research baseline; commands are logged by default.")
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("synthetic", "replay", "live"):
        mode = sub.add_parser(name)
        profile_arguments(mode)
        mode.add_argument("--session", type=Path, help="New output directory (must not exist)")
        mode.add_argument("--seconds", type=float, default=15 if name == "synthetic" else 60)
        mode.add_argument("--keyboard", action="store_true", help="Opt-in Windows keyboard output; live only")
        mode.add_argument("--window-title", help="Required foreground-window title substring for keyboard output")
        if name == "synthetic":
            mode.add_argument("--target", default="forward", help="Target ID or rest")
            mode.add_argument("--seed", type=int, default=42)
        elif name == "replay":
            mode.add_argument("--input", required=True, type=Path, help="Acquisition CSV with companion JSON")
            mode.add_argument("--label", default="", help="Optional replay label")
            mode.add_argument("--stimulus-log", type=Path, help="Matching original stimulus.json for a legacy decoder session")
        else:
            mode.add_argument("--port", required=True)
            mode.add_argument("--montage", help="Montage ID or path to a montage JSON")
            mode.add_argument("--defer-summary", action="store_true", help=argparse.SUPPRESS)
            mode.add_argument("--stop-on-stdin", action="store_true", help=argparse.SUPPRESS)
            mode.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
            output = mode.add_mutually_exclusive_group()
            output.add_argument("--maze-hud", action="store_true", help="Show live scores in the local maze without steering")
            output.add_argument("--maze-control", action="store_true", help="Send accepted commands to the maze; steering starts automatically in the focused game")
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error("--seconds must be positive and at most one hour.")
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Stopped; keyboard released and session log retained.")
        return 130
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, ImportError) as error:
        print(f"Decoder stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
