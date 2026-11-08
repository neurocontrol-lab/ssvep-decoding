"""Desktop launcher for the repeatable PsychoPy and EEG test workflow."""
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .config import read_profile
from .experiment import Experiment, PRESETS, PURPOSES, run_experiment


TESTS = {"Four targets": "four", "Single target": "single", "Rest only": "rest"}


class Launcher:
    def __init__(self, root):
        self.root = root
        self.messages = queue.Queue()
        self.cancel = None
        self.closing = False
        self.last_folder = None
        self.stimulus_path = ""
        root.title("SSVEP Test Launcher")
        root.minsize(790, 700)
        root.geometry("830x760")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        body = ttk.Frame(root, padding=16)
        body.grid(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(4, weight=1)
        ttk.Label(body, text="SSVEP test launcher", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Close the maze and Arduino Serial Monitor/Plotter before starting. Tests log commands without sending keys.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        protocol = ttk.LabelFrame(body, text="Test and display", padding=10)
        protocol.grid(row=2, column=0, sticky="nsew", padx=(0, 6))
        decode = ttk.LabelFrame(body, text="EEG decoder", padding=10)
        decode.grid(row=2, column=1, sticky="nsew", padx=(6, 0))
        self.inputs = {}

        def field(parent, row, label, key, value, choices=None):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
            var = tk.StringVar(value=str(value))
            widget = (ttk.Combobox(parent, textvariable=var, values=choices, state="readonly", width=18)
                      if choices else ttk.Entry(parent, textvariable=var, width=20))
            widget.grid(row=row, column=1, sticky="ew", pady=2)
            parent.columnconfigure(1, weight=1)
            self.inputs[key] = (var, widget)
            return widget

        default = Experiment()
        mode = field(protocol, 0, "Test", "mode", "Four targets", list(TESTS))
        self.target = field(protocol, 1, "Single target", "target", default.target,
                            [t["id"] for t in read_profile("stimulus")["targets"]])
        field(protocol, 2, "Repeats per target", "repeats", default.repeats)
        field(protocol, 3, "Cue (s)", "cue_seconds", default.cue_seconds)
        field(protocol, 4, "Between-cue rest (s)", "rest_seconds", default.rest_seconds)
        field(protocol, 5, "Rest-only duration (s)", "idle_seconds", default.idle_seconds)
        field(protocol, 6, "Screen index", "screen", default.screen)
        self.windowed = tk.BooleanVar(value=False)
        self.window_toggle = ttk.Checkbutton(protocol, text="Windowed stimulus", variable=self.windowed,
                                             command=self.refresh_fields)
        self.window_toggle.grid(row=7, column=0, columnspan=2, sticky="w")
        field(protocol, 8, "Window width (px)", "width", default.width)
        field(protocol, 9, "Window height (px)", "height", default.height)
        field(protocol, 10, "Cue shuffle seed", "seed", default.seed)
        self.display_profile = ttk.Button(protocol, text="Display profile: default 60 Hz", command=self.choose_profile)
        self.display_profile.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        purpose = field(decode, 0, "Run purpose", "purpose", "Diagnostic", [v.capitalize() for v in PURPOSES])
        field(decode, 1, "Run label (optional)", "run_label", "")
        field(decode, 2, "Serial port", "port", os.environ.get("SSVEP_DECODER_PORT") or default.port)
        preset = field(decode, 3, "Preset", "preset", next(iter(PRESETS)), list(PRESETS))
        field(decode, 4, "EEG window (s)", "window_seconds", default.window_seconds)
        field(decode, 5, "Harmonics", "harmonics", default.harmonics)
        field(decode, 6, "Minimum score", "min_score", default.min_score)
        field(decode, 7, "Minimum margin", "min_margin", default.min_margin)
        field(decode, 8, "Persistence (s)", "persistence_seconds", default.persistence_seconds)
        self.purpose_note = tk.StringVar()
        ttk.Label(decode, textvariable=self.purpose_note, wraplength=315, justify="left").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.duration = tk.StringVar()
        ttk.Label(decode, textvariable=self.duration, wraplength=300, justify="left").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)
        self.start_button = ttk.Button(actions, text="Start test", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        self.folder_button = ttk.Button(actions, text="Open session folder", command=self.open_folder, state="disabled")
        self.folder_button.pack(side="left")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(actions, textvariable=self.status, wraplength=400).pack(side="left", padx=10)
        self.results = ScrolledText(body, height=12, wrap="word", font=("Consolas", 10), padx=8, pady=8)
        self.results.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self.show("Results will appear here when the test finishes.")
        mode.bind("<<ComboboxSelected>>", self.change_mode)
        preset.bind("<<ComboboxSelected>>", self.change_preset)
        purpose.bind("<<ComboboxSelected>>", self.change_purpose)
        for key in ("repeats", "cue_seconds", "rest_seconds", "idle_seconds"):
            self.inputs[key][0].trace_add("write", lambda *_: self.update_duration())
        self.refresh_fields()
        self.change_purpose()
        root.after(100, self.poll)

    def value(self, key):
        return self.inputs[key][0].get()

    def change_mode(self, _):
        if self.value("mode") != "Rest only":
            self.inputs["repeats"][0].set("4" if self.value("mode") == "Single target" else "2")
        self.refresh_fields()

    def change_preset(self, _):
        window, harmonics = PRESETS[self.value("preset")]
        self.inputs["window_seconds"][0].set(str(window))
        self.inputs["harmonics"][0].set(str(harmonics))

    def change_purpose(self, _=None):
        purpose = self.value("purpose").lower()
        self.purpose_note.set(f"Save to sessions/{purpose}/. {PURPOSES[purpose]}")

    def refresh_fields(self):
        mode = TESTS[self.value("mode")]
        for key, (_, widget) in self.inputs.items():
            available = self.cancel is None
            if key == "target":
                available &= mode == "single"
            elif key == "idle_seconds":
                available &= mode == "rest"
            elif key in ("repeats", "cue_seconds", "rest_seconds", "screen", "seed", "width", "height"):
                available &= mode != "rest"
                if key in ("width", "height"):
                    available &= self.windowed.get()
            widget.configure(state=("readonly" if isinstance(widget, ttk.Combobox) else "normal")
                             if available else "disabled")
        self.window_toggle.configure(state="normal" if self.cancel is None and mode != "rest" else "disabled")
        self.display_profile.configure(state="normal" if self.cancel is None else "disabled")
        self.update_duration()

    def update_duration(self):
        try:
            mode = TESTS[self.value("mode")]
            if mode == "rest":
                text = f"Recording: {float(self.value('idle_seconds')):g} s without flicker."
            else:
                trials = int(self.value("repeats")) * (4 if mode == "four" else 1)
                seconds = trials * (float(self.value("cue_seconds")) + float(self.value("rest_seconds")))
                text = f"{trials} cue trials · about {seconds:g} s of stimulus and rest, plus EEG lead-in and tail."
            self.duration.set(text)
        except ValueError:
            self.duration.set("Enter valid durations to see the planned length.")

    def choose_profile(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose stimulus profile",
                                          filetypes=[("JSON profiles", "*.json")])
        if not path:
            return
        try:
            profile = read_profile("stimulus", path)
            ids = [t["id"] for t in profile["targets"]]
            if not ids:
                raise ValueError("Profile contains no targets.")
            label = f"Display profile: {profile['refresh_hz']:g} Hz"
        except (OSError, ValueError, TypeError, KeyError) as error:
            messagebox.showerror("Invalid stimulus profile", str(error), parent=self.root)
            return
        self.stimulus_path = path
        self.display_profile.configure(text=label)
        self.target.configure(values=ids)
        if self.value("target") not in ids:
            self.inputs["target"][0].set(ids[0])

    def config(self):
        mode = TESTS[self.value("mode")]
        kwargs = {"mode": mode, "target": self.value("target"), "port": self.value("port"),
                  "purpose": self.value("purpose").lower(), "run_label": self.value("run_label").strip(),
                  "windowed": self.windowed.get(), "stimulus_path": self.stimulus_path}
        for key in ("window_seconds", "min_score", "min_margin", "persistence_seconds"):
            kwargs[key] = float(self.value(key))
        kwargs["harmonics"] = int(self.value("harmonics"))
        if mode == "rest":
            kwargs["idle_seconds"] = float(self.value("idle_seconds"))
        else:
            for key in ("repeats", "screen", "seed"):
                kwargs[key] = int(self.value(key))
            if self.windowed.get():
                kwargs.update(width=int(self.value("width")), height=int(self.value("height")))
            for key in ("cue_seconds", "rest_seconds"):
                kwargs[key] = float(self.value(key))
        return Experiment(**kwargs)

    def start(self):
        try:
            config = self.config()
            config.prepare()
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            messagebox.showerror("Check test settings", str(error), parent=self.root)
            return
        self.cancel = threading.Event()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.folder_button.configure(state="disabled")
        self.refresh_fields()
        self.status.set("Starting…")
        self.show("Running test. Press Escape in PsychoPy or Stop here to end it.")
        stop = self.cancel

        def worker():
            try:
                folder, summary = run_experiment(config, Path("sessions"), stop,
                                                 lambda status: self.messages.put(("status", status)))
                self.messages.put(("done", (folder, summary)))
            except Exception as error:
                self.messages.put(("done", (None, f"Test failed: {error}")))

        threading.Thread(target=worker).start()

    def stop(self):
        if self.cancel is not None:
            self.cancel.set()
            self.stop_button.configure(state="disabled")
            self.status.set("Stopping and saving…")

    def close(self):
        if self.cancel is None:
            self.root.destroy()
        else:
            self.closing = True
            self.stop()

    def show(self, text):
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", text)
        self.results.configure(state="disabled")

    def open_folder(self):
        if self.last_folder:
            if hasattr(os, "startfile"):
                os.startfile(self.last_folder)
            else:
                messagebox.showinfo("Session folder", str(self.last_folder), parent=self.root)

    def poll(self):
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                if not self.cancel.is_set():
                    self.status.set(value)
            else:
                self.cancel = None
                self.last_folder, summary = value
                self.show(summary + (f"\n\nSession: {self.last_folder}" if self.last_folder else ""))
                self.status.set("Session saved" if self.last_folder else "Test failed")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.folder_button.configure(state="normal" if self.last_folder else "disabled")
                self.refresh_fields()
                if self.closing:
                    self.root.destroy()
                    return
        self.root.after(100, self.poll)


def main():
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
