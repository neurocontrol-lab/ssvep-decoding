"""Summaries of acquired samples and approximate stimulus-aligned decisions."""
from bisect import bisect_left
from collections import Counter
import json
import statistics

import numpy as np
from scipy.signal import welch


def read_json(path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def eeg_quality(path, nominal):
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as source:
        header = source.readline().strip().split(",")
        if not source.readline():
            return {}
    if len(header) != 10 or header[:2] != ["sequence", "timestamp_us"]:
        raise ValueError("Unexpected acquisition CSV header")
    x = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    if x.shape[1] != len(header):
        raise ValueError("Incomplete acquisition frame")
    if len(x) < 2:
        return {"samples": len(x)}
    deltas = np.diff(x[:, 1]) % 2**32
    elapsed = float(deltas.sum() / 1e6)
    rate = (len(x)-1) / elapsed if elapsed else 0
    missing = int((np.diff(x[:, 0]) % 2**32 - 1).sum())
    result = {"samples": len(x), "seconds": elapsed, "rate_hz": rate, "missing_slots": missing,
              "missed_deadlines": int(x[-1, 4]), "queue_overflows": int(x[-1, 5])}
    adc = x[:, 6:]
    result["rail_percent"] = dict(zip(header[6:], (100*np.mean((adc <= 1)|(adc >= 4094), axis=0)).tolist()))
    if len(x) >= 4*nominal and nominal > 102 and not missing and abs(rate/nominal-1) < .01:
        f, power = welch(adc, fs=rate, nperseg=min(len(x), round(rate*10)), axis=0)
        line = power[(f >= 49) & (f <= 51)].sum(axis=0)
        total = power[(f >= 1) & (f <= 120)].sum(axis=0)
        fraction = np.divide(100*line, total, out=np.zeros_like(line), where=total > 0)
        rms = np.sqrt(line*(f[1]-f[0]))
        result["near_50hz_percent"] = dict(zip(header[6:], fraction.tolist()))
        result["near_50hz_rms_counts"] = dict(zip(header[6:], rms.tolist()))
    return result


def read_events(path, notes):
    events = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                notes.append("Skipped an incomplete decoder event.")
    return events


def replay_times(events, original, hop):
    # Unwrap relative to the first source decision, including MCU counter rollover.
    original = [row for row in original if "sequence" in row and "host_perf_counter" in row]
    origin = original[0]["sequence"] if original else 0
    modulus = 2**32
    anchors = {(row["sequence"] - origin) % modulus: row["host_perf_counter"]
               for row in original}
    positions = sorted(anchors)
    times = {}
    for event in events:
        sequence = event["sequence"]
        position = (sequence - origin) % modulus
        if position in anchors:
            times[sequence] = anchors[position]
            continue
        i = bisect_left(positions, position)
        if not 0 < i < len(positions):
            continue
        left, right = positions[i-1:i+1]
        if right-left <= 2*hop and anchors[left] < anchors[right]:
            fraction = (position-left)/(right-left)
            times[sequence] = anchors[left] + fraction*(anchors[right]-anchors[left])
    return times


def build_summary(folder, mode, status, problem="", *, decoder_dir=None,
                  raw_path=None, stimulus_path=None, original_dir=None):
    notes = []
    def load(path):
        try:
            return read_json(path, {})
        except (OSError, ValueError):
            notes.append(f"Could not read {path}.")
            return {}
    decoder_dir = decoder_dir or folder / "decoder"
    session = load(decoder_dir / "session.json")
    stimulus_path = stimulus_path or folder / "stimulus/stimulus.json"
    stimulus = load(stimulus_path)
    profiles = session.get("profiles", {})
    profile = profiles.get("decoder") or load(folder / "decoder_profile.json")
    settings = load(folder / "settings.json")
    events = read_events(decoder_dir / "events.jsonl", notes)
    nominal = session.get("metadata", {}).get("sample_rate_hz", 250)
    try:
        quality = eeg_quality(raw_path or decoder_dir / "raw.csv", nominal)
    except (OSError, ValueError) as error:
        quality = {}
        notes.append(f"Raw timing summary unavailable: {error}")
    scored = [e for e in events if e.get("valid") and e.get("scores")]
    emitted = Counter(e["accepted"] for e in events if e.get("emit"))
    peak = max((e["score"] for e in scored), default=None)
    result = {"mode": mode, "purpose": settings.get("purpose", "unclassified"),
              "run_label": settings.get("run_label", ""), "status": status, "problem": problem, "decoder_profile": profile,
              "profiles": profiles,
              "eeg": quality, "decoder_stop": session.get("stop_reason"),
              "scored_windows": len(scored), "peak_score": peak,
              "invalid": dict(Counter(e.get("reason") for e in events if not e.get("valid"))),
              "emitted": dict(emitted), "notes": notes}
    if mode == "rest":
        result["rest"] = {"windows": len(scored), "peak": peak, "emissions": sum(emitted.values())}
        return result

    intervals = stimulus.get("frame_intervals_seconds", [])
    result["display"] = {"status": stimulus.get("status"), "refresh_hz": stimulus.get("observed_refresh_hz"),
                         "reported_drops": stimulus.get("dropped_frames"),
                         "largest_interval_ms": 1000*max(intervals) if intervals else None}
    trials = stimulus.get("trials", [])
    if not trials:
        notes.append("No stimulus markers are available. Scores and emissions are unlabeled.")
        return result
    if original_dir is None:
        alignment = "live decision host timestamps"
    else:
        original = read_events(original_dir / "events.jsonl", notes)
        hop = load(original_dir / "session.json").get("effective_hop_samples", 0)
        times = replay_times(scored, original, hop)
        alignment = "original decision host timestamps matched or interpolated by sample sequence"
    result["alignment"] = alignment
    result["stimulus_log"] = str(stimulus_path.resolve())
    targets = {}
    rest_rows = []
    labeled_emits = 0
    def onset(row):
        return row["host_perf_counter_after_flip"] if "host_perf_counter_after_flip" in row else row["host_perf_counter_before_flip"]
    for i, t in enumerate(trials):
        end = onset(trials[i+1]) if i+1 < len(trials) else stimulus.get("host_perf_counter_end")
        if end is None:
            end = onset(t)+t["frame_count"]/stimulus["stimulus_profile"]["refresh_hz"]
        lo = onset(t)+profile.get("window_seconds", 2)+.25
        q = t["cue"]
        rows = []
        for event in scored:
            timestamp = (event.get("host_perf_counter") if original_dir is None
                         else times.get(event["sequence"]))
            if timestamp is not None and lo <= timestamp < end-.15 and (q is None or q in event["scores"]):
                rows.append(event)
        labeled_emits += sum(e["emit"] for e in rows)
        if q is None:
            rest_rows.extend(rows)
            continue
        target = targets.setdefault(q, {"windows": 0, "ranked_first": 0, "trials_scored": 0,
                                      "correct_emissions": 0, "wrong_emissions": 0, "scores": []})
        target["trials_scored"] += bool(rows)
        target["windows"] += len(rows)
        target["ranked_first"] += sum(max(e["scores"], key=e["scores"].get) == q for e in rows)
        target["correct_emissions"] += sum(e["emit"] and e["accepted"] == q for e in rows)
        target["wrong_emissions"] += sum(e["emit"] and e["accepted"] != q for e in rows)
        target["scores"].extend(e["scores"][q] for e in rows)
    for target in targets.values():
        scores = target.pop("scores")
        target["mean_score"] = statistics.mean(scores) if scores else None
    result["targets"] = targets
    result["rest"] = {"windows": len(rest_rows), "peak": max((e["score"] for e in rest_rows), default=None),
                      "emissions": sum(e["emit"] for e in rest_rows)}
    result["unlabeled_emissions"] = sum(emitted.values())-labeled_emits
    notes.append(f"Cue/rest labels use {alignment} and exclude transitions. "
                 "Overlapping windows are not independent trials.")
    if original_dir is not None and not times:
        notes.append("No replay decisions could be aligned to the original recording.")
    return result


def render_summary(r):
    def score(v):
        return f"{v:.3f}" if v is not None else "—"

    lines = [f"{r['purpose'].capitalize()} · {r['mode']} test — {r['status']}"]
    if r["run_label"]:
        lines.append(f"Run label: {r['run_label']}")
    lines.append(f"Montage: {(r.get('montage') or {}).get('id', 'unspecified')}")
    if r["problem"]:
        lines.append(r["problem"])
    q = r["eeg"]
    if "rate_hz" in q:
        lines.append(f"EEG: {q['seconds']:.2f} s, {q['samples']:,} frames, {q['rate_hz']:.3f} samples/s")
        lines.append(f"Missing slots: {q['missing_slots']}; queue overflows: {q['queue_overflows']}")
        if "near_50hz_rms_counts" in q:
            lines.append("Near-50 Hz RMS (ADC counts): " +
                         "  ".join(f"{k} {v:.1f}" for k, v in q["near_50hz_rms_counts"].items()))
    if r.get("display", {}).get("refresh_hz") is not None:
        d = r["display"]
        lines.append(f"Display: {d['refresh_hz']:.2f} Hz reported, {d['reported_drops']} dropped-frame events")
    lines.append(f"Scored windows: {r['scored_windows']}; rejected updates: {sum(r['invalid'].values())}")
    lines.append(f"Peak CCA: {score(r['peak_score'])}; command emissions: {sum(r['emitted'].values())}")
    if r.get("targets"):
        lines.extend(["", "Cued target       Ranked first      Mean CCA    Correct / wrong emissions"])
        for k, v in r["targets"].items():
            n = v["windows"]
            rank = f"{v['ranked_first']}/{n} ({v['ranked_first']/n:.0%})" if n else "—"
            lines.append(f"{k:16}  {rank:16}  {score(v['mean_score']):8}    "
                         f"{v['correct_emissions']} / {v['wrong_emissions']}")
    if "rest" in r:
        rest = r["rest"]
        lines.extend(["", f"Rest: {rest['windows']} scored windows, peak {score(rest['peak'])}, {rest['emissions']} emissions"])
    if "unlabeled_emissions" in r:
        lines.append(f"Transition / outside-trial emissions: {r['unlabeled_emissions']}")
    lines.extend(["", *r["notes"]])
    return "\n".join(lines)


def save_summary(directory, summary):
    directory.mkdir(parents=True, exist_ok=True)
    text = render_summary(summary)
    for name, contents in (("summary.json", json.dumps(summary, indent=2)), ("summary.txt", text)):
        with (directory / name).open("x", encoding="utf-8") as file:
            file.write(contents + "\n")
    return text
