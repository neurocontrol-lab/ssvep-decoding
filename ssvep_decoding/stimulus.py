"""Separate PsychoPy process for development targets and cued/rest trials."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from .cli import load_profiles, profile_arguments


def bright_at(frame, target):
    return frame % target["period_frames"] < target["bright_frames"]


def trial_plan(target_ids, repeats, cue_seconds, rest_seconds, refresh, seed):
    import random
    rng = random.Random(seed)
    plan = []
    for _ in range(repeats):
        order = list(target_ids)
        rng.shuffle(order)
        for target in order:
            plan.extend([(target, round(cue_seconds*refresh)), (None, round(rest_seconds*refresh))])
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    profile_arguments(parser)
    parser.add_argument("--single", help="Show only this target ID for initial checks")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cue-seconds", type=float, default=6)
    parser.add_argument("--rest-seconds", type=float, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--screen", type=int, default=0)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--allow-refresh-mismatch", action="store_true", help="Preview only; logs mismatch")
    args = parser.parse_args()
    profiles = load_profiles(args)
    s = profiles["stimulus"]
    ids = [target["id"] for target in s["targets"]]
    if args.single and args.single not in ids:
        parser.error("--single must name a target in the stimulus profile.")
    if not 1 <= args.repeats <= 100 or not 0 < args.cue_seconds <= 60 or not 0 < args.rest_seconds <= 60:
        parser.error("Use 1-100 repeats and positive cue/rest durations up to 60 seconds.")
    try:
        from psychopy import event, visual
    except ImportError:
        parser.exit(1, "PsychoPy is optional. See README for installing/running the stimulus environment.\n")
    directory = args.session or Path("sessions") / ("stimulus_"+datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    directory.mkdir(parents=True, exist_ok=False)
    win = None
    records = []
    report = {"stimulus_profile": s, "seed": args.seed, "single": args.single,
              "host_clock": "time.perf_counter; not synchronized to MCU", "status": "starting"}
    try:
        win = visual.Window(size=(1280, 720), fullscr=not args.windowed, screen=args.screen,
                            units="pix", color=-0.6, waitBlanking=True)
        measured = win.getActualFrameRate(nIdentical=20, nMaxFrames=120, nWarmUpFrames=30)
        measured = float(measured) if measured is not None else None
        report["observed_refresh_hz"] = measured
        mismatch = bool(measured is None or abs(measured/s["refresh_hz"]-1) > 0.02)
        report["refresh_mismatch"] = mismatch
        if mismatch and not args.allow_refresh_mismatch:
            raise ValueError(f"Refresh {measured} differs from profile {s['refresh_hz']} Hz or is unstable. Adjust profile; use --allow-refresh-mismatch only for preview.")
        targets = [t for t in s["targets"] if not args.single or t["id"] == args.single]
        size = min(s["target_size_px"], float(win.size[1])*0.18)
        boxes, labels = [], []
        for target in targets:
            position = (target["position"][0]*win.size[0]/2, target["position"][1]*win.size[1]/2)
            boxes.append(visual.Rect(win, width=size, height=size, pos=position, lineColor=0))
            labels.append(visual.TextStim(win, text=target["id"], height=18,
                                          pos=(position[0], position[1]-size/2-20)))
        message = visual.TextStim(win, text="SPACE: start | ESC: exit", height=22, wrapWidth=600)
        message.draw(); win.flip()
        if "escape" in event.waitKeys(keyList=["space", "escape"]):
            report["status"] = "cancelled"
            return 0
        plan = trial_plan([t["id"] for t in targets], args.repeats, args.cue_seconds,
                          args.rest_seconds, s["refresh_hz"], args.seed)
        report["plan"] = plan
        win.flip()  # reset frame timing after the user-controlled start wait
        win.recordFrameIntervals = True
        frame_index = 0
        for trial, (cue, count) in enumerate(plan):
            message.text = f"LOOK: {cue.upper()}" if cue else "REST: look at center"
            marker = {"trial": trial, "cue": cue, "first_frame_index": frame_index,
                      "frame_count": count, "host_perf_counter_before_flip": time.perf_counter()}
            for i in range(count):
                if "escape" in event.getKeys(keyList=["escape"]):
                    report["status"] = "interrupted"
                    return 0
                for target, box, label in zip(targets, boxes, labels):
                    box.fillColor = (1 if bright_at(frame_index, target) else -1) if cue else -0.4
                    box.draw(); label.draw()
                message.draw()
                stamp = win.flip()
                if i == 0:
                    marker["psychopy_flip_time"] = stamp
                    records.append(marker)
                frame_index += 1
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "error"
        report["error"] = str(error)
        raise
    finally:
        if win:
            report["frame_intervals_seconds"] = list(win.frameIntervals)
            report["dropped_frames"] = win.nDroppedFrames
            win.close()
        report["trials"] = records
        (directory / "stimulus.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Stimulus log: {directory.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
