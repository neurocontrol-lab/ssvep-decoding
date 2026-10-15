import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys
import time

from . import __version__
from .config import read_profile, reference_frequency, validate
from .output import Keyboard, KEYS
from .pipeline import Pipeline
from .sources import FIELDS, Replay, SerialSource, Synthetic


def profile_arguments(parser):
    for kind in ("acquisition", "stimulus", "decoder", "control"):
        parser.add_argument("--"+kind, type=Path, help=f"Override {kind} JSON profile")


def load_profiles(args):
    profiles = {kind: read_profile(kind, getattr(args, kind, None))
                for kind in ("acquisition", "stimulus", "decoder", "control")}
    validate(*(profiles[k] for k in ("acquisition", "stimulus", "decoder", "control")))
    return profiles


def run(args):
    profiles = load_profiles(args)
    a, s, d, c = (profiles[k] for k in ("acquisition", "stimulus", "decoder", "control"))
    if args.keyboard and (args.mode != "live" or not args.window_title):
        raise ValueError("Keyboard output requires live mode and --window-title.")
    if args.keyboard and any(key not in KEYS for key in c["action_keys"].values()):
        raise ValueError("Keyboard keys must be w/a/s/d or left/right/up/down.")
    directory = args.session or Path("sessions") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True, exist_ok=False)
    source, keyboard = None, None
    reason, received, updates, commands = "complete", 0, 0, 0
    started = time.perf_counter()
    summary = {"version": __version__, "mode": args.mode, "profiles": profiles,
               "keyboard_enabled": args.keyboard, "window_title": args.window_title,
               "host_start_perf_counter": started,
               "started_at": datetime.now().astimezone().isoformat()}
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
        pipeline = Pipeline(a, s, d, c, source.metadata)
        summary["effective_window_samples"] = pipeline.n
        summary["effective_hop_samples"] = pipeline.hop
        summary["effective_update_seconds"] = pipeline.hop/pipeline.fs
        print(f"Session: {directory.resolve()}\nOutput: {'KEYBOARD ARMED' if keyboard else 'display/log only'}")
        with (directory / "raw.csv").open("x", newline="", encoding="utf-8") as raw, \
                (directory / "events.jsonl").open("x", encoding="utf-8") as events:
            writer = csv.writer(raw)
            writer.writerow(FIELDS+source.metadata["channels"])
            live_start = time.perf_counter()
            for frame in source:
                received += 1
                writer.writerow(frame.row)
                result = pipeline.feed(frame)
                if result is not None:
                    updates += 1
                    result["sequence"] = frame.row[0]
                    result["host_perf_counter"] = time.perf_counter()
                    if keyboard:
                        keyboard.update(result)
                    result["output_mode"] = "keyboard" if keyboard else "log"
                    events.write(json.dumps(result)+"\n")
                    events.flush()
                    if result["emit"]:
                        commands += 1
                        print(f"{result['time_seconds']:7.2f}s {result['accepted']:10} -> {result['action']:10} [{result['key']}] score={result['score']:.3f} margin={result['margin']:.3f}")
                    elif not result["valid"]:
                        print(f"{result['time_seconds']:7.2f}s no command: {result['reason']}")
                if received % 250 == 0:
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
                if source:
                    # Only propagate #end if the source was consumed through EOF.
                    (directory / "raw.json").write_text(json.dumps({"metadata": source.metadata,
                        "end": source.end if reason == "complete" else None}, indent=2), encoding="utf-8")
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
        else:
            mode.add_argument("--port", required=True)
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
