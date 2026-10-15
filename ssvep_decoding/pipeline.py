from collections import deque

import numpy as np
from scipy.signal import butter, iirnotch, sosfilt, sosfilt_zi, tf2sos

from .cca import CCA
from .config import reference_frequency
from .decision import Decision
from .sources import U32


class Pipeline:
    def __init__(self, acquisition, stimulus, decoder, control, metadata):
        self.a, self.d, self.c = acquisition, decoder, control
        self.fs = acquisition["sample_rate_hz"]
        self.n = round(self.fs*decoder["window_seconds"])
        self.hop = max(1, int(self.fs*decoder["update_seconds"]+0.5))
        self.warmup = round(self.fs*decoder["warmup_seconds"])
        self.indices = [6+metadata["channels"].index(c) for c in acquisition["channels"]]
        self.cca = CCA(self.fs, self.n, {t["id"]: reference_frequency(stimulus, t) for t in stimulus["targets"]}, decoder["harmonics"])
        self.sos = butter(decoder["filter_order"], decoder["bandpass_hz"], btype="bandpass", fs=self.fs, output="sos")
        if decoder["notch_hz"] is not None:
            b, a = iirnotch(decoder["notch_hz"], 30, fs=self.fs)
            self.sos = np.vstack((self.sos, tf2sos(b, a)))
        self.decision = Decision(decoder)
        self.previous, self.elapsed = None, 0.0
        self.clear()

    def clear(self):
        self.raw, self.filtered, self.times = deque(maxlen=self.n), deque(maxlen=self.n), deque(maxlen=self.n)
        self.zi = None
        self.count = 0
        self.decision.reset()

    def reject(self, reason):
        self.clear()
        return {"time_seconds": self.elapsed, "valid": False, "reason": reason,
                "scores": {}, "candidate": None, "accepted": None, "emit": False,
                "action": None, "key": None}

    def feed(self, frame):
        row = frame.row
        if self.previous is not None:
            step = (row[0]-self.previous[0]) % U32
            delta = (row[1]-self.previous[1]) % U32
            old = self.previous
            self.previous = row
            if not 0 < step < U32//2 or not 0 < delta < U32//2 or row[4] < old[4] or row[5] < old[5]:
                raise ValueError("Sequence/timestamp reversed or counters reset; restart the session.")
            self.elapsed += delta/1e6
            if step != 1 or row[4:6] != old[4:6]:
                return self.reject("missing_samples")
            if abs(delta-1e6/self.fs) > self.a["max_interval_error_fraction"]*1e6/self.fs:
                return self.reject("timing_jitter")
        else:
            self.previous = row
        if row[2] > self.a["max_scan_fraction"]*1e6/self.fs:
            return self.reject("scan_overrun")
        raw = np.array([row[i] for i in self.indices], dtype=float)
        if self.zi is None:
            self.zi = sosfilt_zi(self.sos)[:, :, None]*raw[None, None, :]
        filtered, self.zi = sosfilt(self.sos, raw[None, :], axis=0, zi=self.zi)
        self.count += 1
        if self.count <= self.warmup:
            return None
        self.raw.append(raw); self.filtered.append(filtered[0]); self.times.append(self.elapsed)
        if len(self.raw) < self.n or (self.count-self.warmup-self.n) % self.hop:
            return None
        raw_window = np.asarray(self.raw)
        spans = np.ptp(raw_window, axis=0)
        rails = np.mean((raw_window <= 1) | (raw_window >= self.a["adc_max"]-1), axis=0)
        observed_rate = (self.n-1)/(self.times[-1]-self.times[0])
        reason = None
        if np.any(spans < self.a["flat_span_counts"]):
            reason = "flat_channel"
        elif np.any(rails > self.a["max_rail_fraction"]):
            reason = "clipped_channel"
        elif abs(observed_rate/self.fs-1) > 0.01:
            reason = "sample_rate_mismatch"
        scores = self.cca.scores(np.asarray(self.filtered)) if reason is None else {}
        decision = self.decision.update(scores, self.elapsed, valid=reason is None)
        accepted = decision["accepted"]
        action = self.c["target_actions"][accepted] if accepted else None
        return {"time_seconds": self.elapsed, "valid": reason is None, "reason": reason,
                "scores": scores, **decision, "action": action,
                "key": self.c["action_keys"][action] if action else None,
                "observed_sample_rate_hz": observed_rate, "ground_truth": frame.label}
