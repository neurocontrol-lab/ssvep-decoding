"""Opt-in Windows key pulses with a separate release watchdog and focus guard."""
import ctypes
import sys
import threading
import time

KEYS = {**{c: ord(c.upper()) for c in "wasd"}, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


class Keyboard:
    def __init__(self, title, pulse):
        if sys.platform != "win32":
            raise ValueError("Keyboard adapter currently supports Windows only.")
        if not title or not title.strip():
            raise ValueError("Keyboard output requires a nonempty --window-title substring.")
        from ctypes import wintypes
        class KeyboardInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]
        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class Union(ctypes.Union):
            _fields_ = [("ki", KeyboardInput), ("mi", MouseInput)]
        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("data", Union)]
        self.Input, self.KeyboardInput, self.Union = Input, KeyboardInput, Union
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        self.title, self.pulse = title.lower(), pulse
        self.key, self.until = None, 0
        self.error = None
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()

    def focused(self):
        text = ctypes.create_unicode_buffer(512)
        self.user.GetWindowTextW(self.user.GetForegroundWindow(), text, len(text))
        return self.title in text.value.lower()

    def send(self, key, up):
        flags = (2 if up else 0) | (1 if key in ("left", "right", "up", "down") else 0)
        event = self.Input(1, self.Union(ki=self.KeyboardInput(KEYS[key], 0, flags, 0, 0)))
        if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise OSError("Windows rejected key injection; check application privilege levels.")

    def release_locked(self):
        if self.key is not None:
            self.send(self.key, True)
            self.key = None

    def update(self, result):
        with self.lock:
            if self.error:
                raise OSError(f"Keyboard watchdog stopped: {self.error}")
            if not result.get("accepted") or not self.focused():
                self.release_locked()
                return
            if result["emit"]:
                self.release_locked()
                key = result["key"]
                if key not in KEYS:
                    raise ValueError(f"Unsupported keyboard key: {key}")
                self.send(key, False)
                self.key, self.until = key, time.monotonic()+self.pulse

    def watch(self):
        try:
            while not self.stop.wait(0.01):
                with self.lock:
                    if self.key and (time.monotonic() >= self.until or not self.focused()):
                        self.release_locked()
        except OSError as error:
            self.error = str(error)
            self.stop.set()

    def close(self):
        self.stop.set()
        self.thread.join()
        with self.lock:
            self.release_locked()
