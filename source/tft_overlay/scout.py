"""Opponent tracking: when you click a player to scout them, the trait panel shows *their* traits.
We notice the trait panel change (cheap pixel check), read the name label over the board, and if
it's an opponent (not you) store their traits. From those traits we guess what each opponent is
building and flag the ones on your comp or your main carry.

Your own name is learned automatically: it's the board label that shows up in nearly every read
(your little legend is on your board, and next to the enemy's in combat).
"""
import difflib
import re
import time
from collections import Counter

import numpy as np

from .reader import PLAYERS, crop

BOARD = (0.18, 0.06, 0.82, 0.80)
THUMB_CHANGED_PX = 30
BIG_CHANGE = 0.3   # share of the trait panel that must change: switching boards, not buying a unit
MIN_GAP_S = 2.0    # at most one scout read per this many seconds


def norm(name):
    """'Xw X₀ X' / 'XwXoX' -> 'xwxox' so OCR variants of a name compare equal."""
    name = name.replace("₀", "o").replace("0", "o")
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Scout:
    def __init__(self, reader, comps, champions, my_name=None):
        self.reader, self.comps, self.champions = reader, comps, champions
        self.fixed_me = norm(my_name) if my_name else None  # config "player_name": always you
        self.lobby = {}          # norm name -> display name (from the player list)
        self.me_counts = Counter()
        self.opponents = {}      # display name -> {"traits", "round", "t"}
        self._thumb = None
        self.pending_at = None
        self.on_opponent = None   # name of the board you're looking at, while it isn't yours
        self.mine = None          # your own traits at the last read of your board (set by the app)
        self._last_read = 0.0

    # ---- names ---------------------------------------------------------------------------
    @property
    def me(self):
        if self.fixed_me:
            return self.fixed_me
        return self.me_counts.most_common(1)[0][0] if self.me_counts else None

    def is_me(self, name):
        me = self.me
        return bool(me) and difflib.SequenceMatcher(None, me, norm(name)).ratio() >= 0.75

    def _labels(self, frame):
        texts = [t for _, _, t in self.reader._lines(crop(frame, BOARD))]
        return {norm(t) for t in texts if len(norm(t)) >= 3}

    def _match(self, label):
        keys = [k for k in self.lobby if not self.is_me(k)]  # your own name can be in the list too
        m = difflib.get_close_matches(label, keys, n=1, cutoff=0.7)
        return m[0] if m else None

    def _opponent(self, labels):
        """Display name of the opponent whose board this is, or None (yours, a combat, a carousel)."""
        me = self.me
        if me and any(difflib.SequenceMatcher(None, me, lab).ratio() >= 0.75 for lab in labels):
            return None  # your own board, or a combat (your legend is there too)
        found = {self._match(lab) for lab in labels} - {None}
        return self.lobby[found.pop()] if len(found) == 1 else None

    def record(self, name, traits, round_):
        if traits and not self.is_me(name):
            self.opponents[name] = {"traits": traits, "round": round_, "t": time.time()}

    def reset(self):
        """New game: different lobby, nobody scouted yet (your own name is kept)."""
        self.lobby.clear()
        self.opponents.clear()

    @property
    def sure_of_me(self):
        if self.fixed_me:
            return True
        top = self.me_counts.most_common(2)
        return bool(top) and top[0][1] >= 3 and (len(top) == 1 or top[0][1] >= top[1][1] + 2)

    def learn(self, frame):
        """At each full read: refresh the lobby names (from the player list OCR the HP read already
        did) and, until your name is certain, count which board label is always there.
        Returns the opponent's name when you're looking at someone else's board."""
        for _, _, _, t in self.reader.boxes(crop(frame, PLAYERS), "players"):
            n = norm(t)
            if len(n) >= 3 and not n.isdigit():
                # keep the longest reading of each name (the list sometimes cuts names off)
                key = next((k for k in self.lobby if k in n or n in k), n)
                if len(n) >= len(key):
                    self.lobby.pop(key, None)
                    self.lobby[n] = t.strip()
        if self.sure_of_me:  # no board OCR: the scout's own reads track whose board this is
            return self.on_opponent
        labels = self._labels(frame)
        # your label is on your board in (nearly) every read; an opponent's only when you scout
        # them or fight them. The most frequent one is you, whether or not the player list shows it.
        for lab in labels:
            self.me_counts[lab] += 1
        return self._opponent(labels) if self.sure_of_me else None

    # ---- scouting ------------------------------------------------------------------------
    def tick(self, trait_img):
        """Cheap: called every loop with a grab of the trait panel. Schedules a scout read when it
        changes (you switched boards, or your own traits changed)."""
        thumb = trait_img[::4, ::4].mean(axis=2).astype(np.float32)
        if self._thumb is not None and self._thumb.shape == thumb.shape:
            changed = np.count_nonzero(np.abs(self._thumb - thumb) > THUMB_CHANGED_PX) / thumb.size
            if changed >= BIG_CHANGE:  # a different board's traits, not one unit added to yours
                self.pending_at = max(time.time() + 0.6, self._last_read + MIN_GAP_S)
        self._thumb = thumb

    def due(self):
        return self.pending_at is not None and time.time() >= self.pending_at

    def read(self, frame, round_):
        """Full frame after a trait-panel change: if you're looking at one opponent's board (their
        label, not yours), store their traits. Returns the opponent's name or None."""
        self.pending_at = None
        self._last_read = time.time()
        name = self._opponent(self._labels(frame))
        self.on_opponent = name
        if not name:
            return None
        traits = self.reader.read_traits(frame)
        if traits and traits == self.mine:  # your own board with your label hidden, not theirs
            self.on_opponent = None
            return None
        self.record(name, traits, round_)
        return name if traits else None

    # ---- contest -------------------------------------------------------------------------
    def contest(self, comp, suggest):
        """-> [(opponent, their likely comp, why)] for opponents on your comp or your main carry.
        `suggest` is matcher.suggest bound to the comps/champions."""
        main = next(iter(comp.get("carries") or {}), None)
        base = comp["name"].split(" — ")[0]
        out = []
        for name, info in self.opponents.items():
            if self.is_me(name):
                continue
            traits = info["traits"]
            # evidence, not a guess: enough units to tell what they're building, a confident fit,
            # and they can actually be fielding your carry (all its traits show on their board)
            if sum(traits.values()) < 5 or not main or not self._could_field(main, traits):
                continue
            best = suggest(traits)
            if not best or best[0]["score"] < 0.45:
                continue
            theirs = best[0]["comp"]
            their_main = next(iter(theirs.get("carries") or {}), None)
            if theirs["name"].split(" — ")[0] == base:
                out.append((name, theirs["name"], f"same comp, seen {info['round'] or '?'}"))
            elif main and (main == their_main or main in theirs.get("units", [])[:3]):
                out.append((name, theirs["name"], f"also uses {main}, seen {info['round'] or '?'}"))
        return out

    def _could_field(self, unit, traits):
        ch = self.champions.get(unit)
        return bool(ch) and all(traits.get(t, 0) >= 1 for t in ch["traits"])

    def lobby_summary(self, suggest):
        """[(opponent, likely comp, round seen)] for everyone scouted so far."""
        rows = []
        for name, info in sorted(self.opponents.items()):
            if self.is_me(name):
                continue
            best = suggest(info["traits"])
            rows.append((name, best[0]["comp"]["name"] if best else "?", info["round"]))
        return rows
