"""Stall watchdog for the read loop: if the loop stops coming round for `stall_s` seconds, every thread's stack
goes into data/overlay_errors.log ("overlay stalled 30s"), so a freeze shows what it was stuck on, and
"recovered after Ns" follows if it comes back. Added after the overlay went quiet at 1-3 with no error logged
(2026-10-27).

A Python thread does the watching; nearly every blocking call (screen grab, OCR, file writes) lets it run. A
call that holds the interpreter lock would stop it too, so it also keeps faulthandler's own C-level timer armed
(`backstop_s`): if the watcher itself stops, that dumps the stacks under a "Timeout (0:01:30)!" line.

Also `note(text)`: one timestamped line in the same log (the game window lost and found, pause pressed)."""
import faulthandler
import threading
import time

EVERY = 5.0


class Watchdog:
    def __init__(self, path, stall_s=30.0, backstop_s=90.0):
        self.path, self.stall_s, self.backstop_s = path, stall_s, backstop_s
        self.beat_t = time.time()
        self.stalled = None  # during a logged stall: the loop's last beat before it
        self._f = None
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._watch, daemon=True, name="watchdog").start()
        return self

    def beat(self):
        """Call once per loop."""
        self.beat_t = time.time()

    def note(self, text):
        with self._lock:
            try:
                f = self._file()
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
                f.flush()
            except OSError:
                pass

    def _file(self):
        if self._f is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = open(self.path, "a", encoding="utf8")
        return self._f

    def _watch(self):
        while True:
            try:
                with self._lock:
                    faulthandler.dump_traceback_later(self.backstop_s, file=self._file())
            except (OSError, ValueError):
                pass
            idle = time.time() - self.beat_t
            if idle >= self.stall_s and self.stalled is None:
                self.stalled = self.beat_t  # the last time the loop came round
                self.note(f"overlay stalled {idle:.0f}s: the read loop hasn't come round. Stacks:")
                with self._lock:
                    try:
                        faulthandler.dump_traceback(file=self._file(), all_threads=True)
                        self._f.write("\n")
                        self._f.flush()
                    except (OSError, ValueError):
                        pass
            elif idle < self.stall_s and self.stalled is not None:
                self.note(f"overlay recovered after {self.beat_t - self.stalled:.0f}s")
                self.stalled = None
            time.sleep(EVERY)
