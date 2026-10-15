import json
import math
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "profiles"
DEFAULTS = {"acquisition": "acquisition_mg24.json", "stimulus": "stimulus_60hz.json",
            "decoder": "decoder_default.json", "control": "control_navigation.json"}


def read_profile(kind, path=None):
    return json.loads(Path(path or PROFILE_DIR / DEFAULTS[kind]).read_text(encoding="utf-8"))


def reference_frequency(stimulus, target):
    measured = target.get("reference_hz")
    return measured if measured is not None else stimulus["refresh_hz"] / target["period_frames"]


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate(a, s, d, c):
    if not positive(a["sample_rate_hz"]) or not 1 <= len(a["channels"]) <= 4 or len(set(a["channels"])) != len(a["channels"]):
        raise ValueError("Select 1-4 distinct channels and a positive sample rate.")
    if a["adc_max"] != 4095 or not positive(a["baud"]):
        raise ValueError("MG24 v1 input requires 12-bit ADC counts and a positive baud rate.")
    for key in ("max_interval_error_fraction", "max_scan_fraction", "max_rail_fraction"):
        if not 0 < a[key] <= 1:
            raise ValueError(f"Invalid acquisition tolerance: {key}")
    if not positive(a["flat_span_counts"]):
        raise ValueError("Flat-signal range threshold must be positive.")
    if not positive(a["max_live_lag_seconds"]):
        raise ValueError("Live lag limit must be positive.")
    if not positive(s["refresh_hz"]) or not positive(s["target_size_px"]) or len(s["targets"]) != 4:
        raise ValueError("Stimulus profile requires four targets, positive refresh and size.")
    ids, frequencies = [], []
    for target in s["targets"]:
        ids.append(target["id"])
        period, bright = target["period_frames"], target["bright_frames"]
        if not isinstance(target["id"], str) or not target["id"] or type(period) is not int or type(bright) is not int or not 0 < bright < period:
            raise ValueError("Invalid target ID or bright/dark frame sequence.")
        if len(target["position"]) != 2 or not all(math.isfinite(v) and abs(v) <= 1 for v in target["position"]):
            raise ValueError("Target positions must be within normalized screen bounds.")
        frequency = reference_frequency(s, target)
        if not positive(frequency):
            raise ValueError("Target reference frequency must be positive.")
        frequencies.append(frequency)
    if len(set(ids)) != len(ids) or len(set(frequencies)) != len(frequencies):
        raise ValueError("Target IDs and reference frequencies must be distinct.")
    fs = a["sample_rate_hz"]
    if type(d["harmonics"]) is not int or not 1 <= d["harmonics"] <= 5 or type(d["filter_order"]) is not int or not 1 <= d["filter_order"] <= 8:
        raise ValueError("Use 1-5 harmonics and filter order 1-8.")
    low, high = d["bandpass_hz"]
    if not 0 < low < high < fs / 2:
        raise ValueError("Bandpass must lie below the sample-rate Nyquist limit.")
    if min(frequencies) < low or max(frequencies)*d["harmonics"] > high:
        raise ValueError("Bandpass must include every configured reference harmonic.")
    notch = d["notch_hz"]
    if notch is not None and (not positive(notch) or notch >= fs/2):
        raise ValueError("Notch must lie below Nyquist.")
    for key in ("window_seconds", "update_seconds", "warmup_seconds", "repeat_seconds"):
        if not positive(d[key]):
            raise ValueError(f"Decoder setting must be positive: {key}")
    if not 0 < d["update_seconds"] <= d["window_seconds"] or not 0 <= d["persistence_seconds"] < 60:
        raise ValueError("Invalid update interval or persistence duration.")
    if int(round(d["window_seconds"]*fs)) <= 2*d["harmonics"] + len(a["channels"]):
        raise ValueError("Window too short for configured CCA dimensions.")
    if not 0 <= d["min_score"] <= 1 or not 0 <= d["min_margin"] <= 1:
        raise ValueError("Score and margin thresholds must be within [0, 1].")
    if set(c["target_actions"]) != set(ids):
        raise ValueError("Control mapping must cover exactly the configured targets.")
    if any(action not in c["action_keys"] for action in c["target_actions"].values()):
        raise ValueError("Every action must have a key mapping.")
    if not 0 < c["key_pulse_seconds"] <= 0.5:
        raise ValueError("Key pulses must be greater than zero and at most 0.5 seconds.")
    if c["key_pulse_seconds"] >= d["repeat_seconds"]:
        raise ValueError("Key pulse must be shorter than the repeat interval.")
