"""Tocker's Trials: screenshots of every round's enemy board, saved to data/tockers/<run>/ with a shots.json
(round, lives, level, gold, your traits), so each round's enemies, star levels and damage can be worked out
after a run. Riot doesn't ship Set 18's Tocker's lineups in any readable game file, so this is the only source.

Per round: "planning" 2s in, "fight" 1s after TFT's "Combat" banner and "midfight" 8s after it; if no banner
was seen 25s into the round, one "late" shot instead. Shots are kept until the mode is known: a lobby game
(3-1 without Tocker's rounds 2-8 to 2-10 seen) deletes its folder. Only the newest `keep` runs are kept."""
import json
import re
import shutil
import threading
import time
from pathlib import Path

from PIL import Image

PLAN_AFTER, LATE_AFTER = 2.0, 25.0   # seconds after the round number changed
FIGHT_AFTER, MID_AFTER = 1.0, 8.0    # seconds after the Combat banner


def _key(round_):
    m = re.match(r"(\d)-(\d+)", round_ or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


class TockerShots:
    def __init__(self, root, keep=8, quality=88):
        self.root, self.keep, self.quality = Path(root), keep, quality
        self.folder, self.log = None, []
        self.round, self.combat, self.due, self.off = None, False, [], False
        self.first = None  # the first round this run was seen at
        self._lock, self._written = threading.Lock(), {}  # shots.json: never overwrite a longer log
        self._dead = set()  # folders of lobby games: a shot still being written is deleted too

    def tick(self, now, round_, combat, tockers, grab, reading=None):
        """Call every loop. round_: the round on screen (None if unread); combat: this round's Combat banner
        showed; tockers: this game is known to be Tocker's; grab(): the full RGB frame or None; reading: the
        last full read (for lives, level, gold and traits in shots.json)."""
        if round_ and round_ != self.round:
            if self.round is None or (round_.startswith("1-") and _key(round_) < _key(self.round)):
                self._new_run()  # a new game, or a restart
                self.first = round_
            self.round, self.combat = round_, False
            self.due = [(now + PLAN_AFTER, "planning"), (now + LATE_AFTER, "late")]
        if combat and self.round and not self.combat:
            self.combat = True
            self.due = [(now + FIGHT_AFTER, "fight"), (now + MID_AFTER, "midfight")]
        # a lobby game: stage 4 (Tocker's ends at 3-10), or 3-1 without Tocker's rounds 2-8 to 2-10 when this run
        # was seen from stage 1 or 2 (an overlay restart mid-stage-3 hasn't seen them yet)
        if self.round and not tockers and (_key(self.round) >= (4, 1) or
                                           (_key(self.round) >= (3, 1) and _key(self.first) < (3, 1))):
            if self.folder and not self.off:  # a lobby game, not Tocker's
                with self._lock:
                    self._dead.add(self.folder)
                    shutil.rmtree(self.folder, ignore_errors=True)
            self.off = True
        if self.off or not self.due or now < self.due[0][0]:
            return
        _, tag = self.due.pop(0)
        if tag == "late" and self.combat:
            return
        frame = grab()
        if frame is not None:
            self._save(frame, tag, reading or {})

    def _new_run(self):
        self.folder, self.log, self.off = self.root / time.strftime("%Y%m%d-%H%M%S"), [], False

    def _save(self, frame, tag, reading):
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{self.round}_{tag}.jpg"
        econ = reading.get("econ") or {}
        self.log.append({"round": self.round, "shot": tag, "file": name, "time": time.strftime("%H:%M:%S"),
                         "lives": econ.get("hp"), "level": econ.get("level"), "gold": econ.get("gold"),
                         "traits": reading.get("traits") or {}})
        folder, log = self.folder, list(self.log)

        def write():  # JPEG encode off the read loop: no stall
            try:
                Image.fromarray(frame).convert("RGB").save(folder / name, quality=self.quality)
                with self._lock:
                    if folder in self._dead:
                        shutil.rmtree(folder, ignore_errors=True)
                        return
                    if len(log) > self._written.get(folder, 0):
                        (folder / "shots.json").write_text(json.dumps(log, indent=1), encoding="utf8")
                        self._written[folder] = len(log)
                    runs = sorted(p for p in self.root.iterdir() if p.is_dir())
                    for old in runs[:-self.keep]:
                        if old != folder:
                            shutil.rmtree(old, ignore_errors=True)
            except OSError:
                pass  # the folder was just deleted (a lobby game) or the disk is full: skip this shot
        threading.Thread(target=write, daemon=True).start()
