"""Tocker's fights, sampled every 2 seconds into data/tockers/<run>/fights.jsonl, so a freeze can be told apart:

- "clock": the fight clock in the top bar. If it stands still while real time passes, the game itself froze
  (your PC or the game client); if it keeps counting while the board stands still, the fight stalled on the
  server's side.
- "board_moved": % of the board that changed since the last sample (near 0 = nothing moving).
- "overlay_ms": the overlay's longest loop since the last sample (a full screen read shows up here), and
  "overlay_cpu" (% of one core) / "pc_cpu" (% of the whole PC) over the same 2 seconds.

A sample where the clock or the board stood still gets "freeze": "clock" / "board". Cheap by design: one
one-line read of a tiny crop and a thumbnail diff every 2 seconds, only during fights."""
import ctypes
import json
import re
import time
from ctypes import wintypes

import cv2
import numpy as np

CLOCK = (0.594, 0.0, 0.651, 0.037)  # "⏱ 24 ^" in the top bar during a fight
BOARD = (0.25, 0.12, 0.75, 0.70)    # where the fight happens
EVERY = 2.0


def _sys_times():
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    f = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
    return f(idle), f(kernel) + f(user)  # kernel time includes idle time


def parse_clock(text):
    """'①24^' / '024^' -> 24: the clock icon reads as ① or a leading 0."""
    m = re.search(r"[0-9]+", text or "")
    if not m:
        return None
    s = m.group(0)
    if len(s) > 1 and s[0] == "0":
        s = s[1:]
    n = int(s)
    return n if n <= 60 else None


class PauseLog:
    def __init__(self, reader):
        self.reader = reader
        self.next, self.round = 0.0, None
        self.prev = None  # (wall time, clock, board thumbnail, process cpu, system times)
        self.last_tick, self.max_gap = None, 0.0

    def tick(self, now, combat, round_, folder, grab):
        """Call every loop. combat: this round's fight is on; folder: the run's shot folder (None: not logging);
        grab(region): an RGB crop of the game."""
        if self.last_tick is not None:  # the overlay's own loop: a slow one means it was busy
            self.max_gap = max(self.max_gap, now - self.last_tick)
        self.last_tick = now
        if round_ != self.round:
            self.round, self.prev = round_, None
        if not combat or folder is None or now < self.next:
            if not combat:
                self.prev = None
            return
        self.next = now + EVERY
        clock_img, board_img = grab(CLOCK), grab(BOARD)
        if clock_img is None or board_img is None:
            return
        clock = parse_clock(self.reader.text(cv2.copyMakeBorder(np.ascontiguousarray(clock_img), 8, 8, 8, 8,
                                                                cv2.BORDER_REPLICATE)))
        thumb = board_img[::8, ::8].mean(axis=2).astype(np.float32)
        cpu, sys_t = time.process_time(), _sys_times()
        entry = {"time": time.strftime("%H:%M:%S"), "round": round_, "clock": clock}
        if self.prev:
            t0, clock0, thumb0, cpu0, sys0 = self.prev
            wall = max(now - t0, 1e-3)
            if thumb0.shape == thumb.shape:
                entry["board_moved"] = round(100 * float(np.count_nonzero(np.abs(thumb - thumb0) > 12)) / thumb.size, 1)
            entry["overlay_ms"] = round(self.max_gap * 1000)
            entry["overlay_cpu"] = round(100 * (cpu - cpu0) / wall)
            if sys_t and sys0 and sys_t[1] > sys0[1]:
                entry["pc_cpu"] = round(100 * (1 - (sys_t[0] - sys0[0]) / (sys_t[1] - sys0[1])))
            if clock is not None and clock0 is not None and clock == clock0 and clock > 1:  # (1 = the fight is over)
                entry["freeze"] = "clock"  # the fight clock didn't move in 2 seconds
            elif clock is not None and entry.get("board_moved", 100) < 0.5:  # (no clock: the fight is over)
                entry["freeze"] = "board"  # clock running, nothing on the board moving
        self.prev = (now, clock, thumb, cpu, sys_t)
        self.max_gap = 0.0
        try:
            with (folder / "fights.jsonl").open("a", encoding="utf8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # folder deleted (a lobby game) or not made yet
