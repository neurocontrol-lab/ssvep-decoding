"""Input adapters for the ssvep-acquisition v1 contract; no sibling imports."""
import csv
from dataclasses import dataclass
import json
import time

import numpy as np

FIELDS = ["sequence", "timestamp_us", "scan_us", "late_us", "missed_deadlines", "queue_overflows"]
U32 = 1 << 32


@dataclass
class Frame:
    row: tuple
    label: str = None


def check_metadata(meta, acquisition):
    if meta.get("protocol") != "ssvep-eeg" or meta.get("version") != 1 or meta.get("adc_bits") != 12:
        raise ValueError("Expected ssvep-acquisition ssvep-eeg protocol v1 with 12-bit ADC counts.")
    channels = meta.get("channels", [])
    if len(channels) != 4 or len(set(channels)) != 4 or not all(isinstance(c, str) for c in channels):
        raise ValueError("Input metadata must name four distinct channels.")
    if not set(acquisition["channels"]).issubset(channels):
        raise ValueError("Selected channels are absent from input metadata.")
    if meta.get("sample_rate_hz") != acquisition["sample_rate_hz"]:
        raise ValueError("Input nominal sample rate differs from the acquisition profile.")
    return FIELDS + channels


def parse_row(text):
    values = text.split(",")
    if len(values) != 10 or not all(v.isascii() and v.isdecimal() for v in values):
        raise ValueError("Malformed acquisition row.")
    row = tuple(map(int, values))
    if any(v >= U32 for v in row[:6]) or any(v > 4095 for v in row[6:]):
        raise ValueError("Acquisition counter or ADC value out of range.")
    return row


class Replay:
    def __init__(self, path, acquisition):
        sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.metadata = sidecar["metadata"]
        expected = check_metadata(self.metadata, acquisition)
        self.file = path.open(newline="", encoding="utf-8")
        self.reader = csv.reader(self.file)
        if next(self.reader, None) != expected:
            self.file.close()
            raise ValueError("CSV header does not match acquisition metadata.")
        self.end = sidecar.get("end")

    def __iter__(self):
        for row in self.reader:
            yield Frame(parse_row(",".join(row)))

    def close(self):
        self.file.close()


class Synthetic:
    def __init__(self, acquisition, frequencies, target, seconds, seed=42):
        self.metadata = {"protocol": "ssvep-eeg", "version": 1, "adc_bits": 12,
                         "sample_rate_hz": acquisition["sample_rate_hz"], "channels": ["D1", "D2", "D3", "D4"]}
        # Generated sources also follow non-default selected pin identities.
        names = list(acquisition["channels"])
        self.metadata["channels"] = names + [f"unused{i}" for i in range(4-len(names))]
        self.fs, self.frequencies, self.target, self.seconds, self.seed = acquisition["sample_rate_hz"], frequencies, target, seconds, seed
        if target not in frequencies and target != "rest":
            raise ValueError("Synthetic target must be a configured ID or 'rest'.")
        self.end = None

    def __iter__(self):
        rng = np.random.default_rng(self.seed)
        count = int(self.seconds*self.fs)
        for i in range(count):
            t = i/self.fs
            signal = np.zeros(4)
            if self.target != "rest":
                phase = np.array([0.1, 0.5, 0.9, 1.2])
                omega = 2*np.pi*self.frequencies[self.target]*t
                signal = 75*np.sin(omega+phase) + 20*np.sin(2*omega+phase)
            values = np.clip(np.rint(2048+signal+rng.normal(0, 20, 4)), 0, 4095).astype(int)
            yield Frame((i, round(t*1e6) % U32, 600, 0, 0, 0, *map(int, values)), self.target)
        self.end = {"slots": count, "acquired": count, "missed_deadlines": 0, "queue_overflows": 0}

    def close(self):
        pass


class SerialLines:
    def __init__(self, port):
        self.port, self.buffer = port, bytearray()

    def line(self, timeout=1):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                return line.decode("ascii").rstrip("\r")
            self.buffer.extend(self.port.read(self.port.in_waiting or 1))
            if len(self.buffer) > 16384 and b"\n" not in self.buffer:
                raise ValueError("Oversized serial record.")
        raise TimeoutError("Acquisition stream stalled; output has been disarmed.")


class SerialSource:
    def __init__(self, port_name, acquisition):
        import serial
        self.end = None
        self.max_lag = acquisition["max_live_lag_seconds"]
        self.origin_host, self.previous_stamp, self.elapsed_mcu = None, None, 0.0
        self.port = serial.Serial(port_name, acquisition["baud"], timeout=0.1, write_timeout=1)
        try:
            time.sleep(2)
            self.port.reset_input_buffer()
            self.reader = SerialLines(self.port)
            self.port.write(b"s\n")
            deadline = time.monotonic()+10
            while not self.reader.line(2).startswith("#end "):
                if time.monotonic() > deadline:
                    raise TimeoutError("Could not stop the prior acquisition session.")
            self.port.write(b"r\n")
            first = self.reader.line(5)
            if not first.startswith("#meta "):
                raise ValueError("Upload eeg_streamer; expected #meta JSON.")
            self.metadata = json.loads(first[6:])
            expected = check_metadata(self.metadata, acquisition)
            if self.reader.line() != ",".join(expected):
                raise ValueError("Serial header does not match metadata.")
        except BaseException:
            self.close()
            raise

    def __iter__(self):
        while True:
            line = self.reader.line()
            if line.startswith("#end "):
                self.end = json.loads(line[5:])
                return
            if line.startswith("#"):
                raise ValueError("Unexpected session marker; possible MCU reset.")
            row = parse_row(line)
            now = time.perf_counter()
            if self.origin_host is None:
                self.origin_host = now
            else:
                delta = (row[1]-self.previous_stamp) % U32
                if not 0 < delta < U32//2:
                    raise ValueError("Live MCU timestamp reversed/reset.")
                self.elapsed_mcu += delta/1e6
                if now-self.origin_host-self.elapsed_mcu > self.max_lag:
                    raise TimeoutError("Live input backlog exceeded the configured lag limit.")
            self.previous_stamp = row[1]
            yield Frame(row)

    def close(self):
        try:
            if self.port.is_open:
                self.port.write(b"s\n")
        finally:
            self.port.close()
