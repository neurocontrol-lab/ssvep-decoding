"""Send live decision summaries to the local Windows maze (UDP SSVEP1)."""
import socket
import sys
import time

from .config import reference_frequency

ADDRESS = ("127.0.0.1", 46081)
ACTIONS = ("forward", "backward", "turn_left", "turn_right")


class MazeOutput:
    def __init__(self, stimulus, control, steering=False):
        if sys.platform != "win32":
            raise ValueError("The maze link uses the Windows performance counter.")
        self.actions = control["target_actions"]
        frequencies = {self.actions[t["id"]]: reference_frequency(stimulus, t)
                       for t in stimulus["targets"]}
        if len(stimulus["targets"]) != len(ACTIONS) or set(frequencies) != set(ACTIONS):
            raise ValueError("Maze output needs one target for each of forward, backward, turn_left and turn_right.")
        self.frequencies = " ".join(f"{frequencies[a]:.6f}" for a in ACTIONS)
        self.steering = steering
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.last_send = -1.0

    def send(self, state, winner="-", score=0.0, margin=0.0, accepted="-", emit=False):
        now = time.perf_counter()
        packet = (f"SSVEP1 {int(now * 1000)} {state} {winner} {score:.6f} {margin:.6f} "
                  f"{accepted} {int(self.steering)} {int(emit)} {self.frequencies}")
        self.last_send = now
        try:
            self.socket.sendto(packet.encode("ascii"), ADDRESS)
        except OSError:
            # A missed datagram must not interrupt sampling or delay the decoder.
            # The game drops its control state if updates stop.
            pass

    def warm(self):
        if time.perf_counter() - self.last_send >= 0.25:
            self.send("warm")

    def update(self, result):
        scores = result["scores"]
        if not result["valid"] or not scores:
            self.send("invalid")
            return
        winner = max(scores, key=scores.get)
        accepted = self.actions[result["accepted"]] if result["accepted"] else "-"
        self.send("score", self.actions[winner], result["score"], result["margin"],
                  accepted, result["emit"])

    def close(self):
        try:
            self.send("stop")
        finally:
            self.socket.close()
