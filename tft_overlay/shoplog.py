"""Records every shop you see (including each reroll of a roll-down) with your level and the
posted shop odds, for checking the shop against its own numbers afterwards
(tools/shop_report.py). Nothing is shown in game.

Cheap while you play: each tick compares a small strip of pixels (the champion names) with the
last one. Changed shops are kept as small images and only read once things go quiet — no OCR
runs in the middle of a roll-down. Reading uses recognition only (the text positions are fixed),
~0.1s per shop on one thread.
"""
import json
from collections import Counter
import re
import time
from pathlib import Path

import numpy as np

from .hud import LAYOUTS, dx, set_dx, shift
from .reader import crop

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "shop_log"

# fractions of the game frame (measured at 1920x1080)
NAMES = (0.285, 0.955, 0.815, 0.995)   # champion names on the 5 shop cards
LEVEL = (0.178, 0.815, 0.245, 0.850)   # "Lvl.6"
ODDS = (0.285, 0.815, 0.450, 0.850)    # "30% 40% 25% 5% 0%"
GOLD = (0.515, 0.815, 0.565, 0.850)    # "25"
# the logger only grabs this band of the screen (shop + level/odds/gold bar) each tick
BAND = (0.12, 0.81, 0.82, 1.0)  # wide enough for both bottom-bar layouts (hud.py)
CHANGED = 6.0     # mean abs pixel diff of the names strip that counts as a new shop
QUIET_S = 3.0     # read pending shops after this long without changes
MAX_PENDING = 80


def is_new_shop(prev, shop):
    """A reroll or new round, as opposed to the same shop redrawn (hover, animation) or the same
    shop with units bought out of it: some slot shows a different champion, or more units show
    than before."""
    if any(a and b and a != b for a, b in zip(prev, shop)):
        return True
    return sum(1 for u in shop if u) > sum(1 for u in prev if u)


class ShopLogger:
    def __init__(self, reader, champions, log=True):
        """log=False: no file, only the quick shop read (board flash) and per-game counts."""
        self.reader, self.champions = reader, champions
        self.rec = reader.rec  # recognition-only engine (no text detection), 1 thread
        self.log = log
        self._q_prev, self._q_dirty = None, True
        self.names_list = list(champions)
        self._last_thumb = None
        self._last_change = 0.0
        self.pending = []          # (t, round, names_img, level_img, odds_img, gold_img)
        self.last_shop = None      # last logged shop (list of 5 names/None)
        self.owned = {}            # unit -> copies bought this game (approximate: sells aren't seen)
        self.complete = False      # saw this game from stage 1: a unit missing from `owned` wasn't bought
        self.seen = Counter()      # unit -> times it appeared in a logged shop this game
        self.slots = Counter()     # cost -> shop slots of that cost logged this game
        self.logged = 0
        self._game_round = None
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"

    @staticmethod
    def _sub(band, region, d=None):
        """Crop `region` (old-layout fractions of the full frame, moved to the layout in use: hud.py) out of a
        BAND grab."""
        bx0, by0, bx1, by1 = BAND
        x0, y0, x1, y1 = shift(region, d)
        return crop(band, ((x0 - bx0) / (bx1 - bx0), (y0 - by0) / (by1 - by0),
                           (x1 - bx0) / (bx1 - bx0), (y1 - by0) / (by1 - by0)))

    def quick(self, band):
        """The 5 shop names right after the shop changes and settles (one tick later), else None.
        Five recognition-only reads, ~0.1s: fast enough to flash buys while you roll."""
        strip = self._sub(band, NAMES)
        if strip.size == 0:
            return None
        thumb = strip[::3, ::3].mean(axis=2).astype(np.float32)
        settled = (self._q_prev is not None and self._q_prev.shape == thumb.shape
                   and np.abs(self._q_prev - thumb).mean() <= CHANGED / 3)
        self._q_prev = thumb
        if not settled:
            self._q_dirty = True
            return None
        if not self._q_dirty:
            return None
        self._q_dirty = False
        return self.names(band)

    def names(self, band):
        """The 5 shop card names (None for empty/unreadable slots) from a BAND grab. Fewer than 2 read: try the
        other bottom-bar layout (hud.py) and switch to it when it reads more."""
        best = self._names(band, dx())
        if sum(1 for n in best if n) < 2:
            for d in LAYOUTS:
                if d == dx():
                    continue
                other = self._names(band, d)
                if sum(1 for n in other if n) >= 2 and sum(1 for n in other if n) > sum(1 for n in best if n):
                    set_dx(d)
                    best = other
        return best

    def _names(self, band, d):
        strip = self._sub(band, NAMES, d)
        w = strip.shape[1]
        out = []
        for k in range(5):  # name sits in the left ~72% of each card (cost on the right)
            text = self._text(strip[:, int(k * w / 5):int(k * w / 5 + w / 5 * 0.72)])
            out.append(self.reader._match(text, self.names_list, cutoff=0.75) if text else None)
        return out

    def tick(self, band, round_):
        """band: grab of BAND (numpy RGB). Only does a pixel diff unless the shop changed."""
        strip = self._sub(band, NAMES)
        if strip.size == 0:
            return
        thumb = strip[::3, ::3].mean(axis=2).astype(np.float32)
        changed = (self._last_thumb is None or self._last_thumb.shape != thumb.shape
                   or np.abs(self._last_thumb - thumb).mean() > CHANGED)
        self._last_thumb = thumb
        if changed:
            self._last_change = time.time()
            self.pending.append((time.time(), round_, strip.copy(), self._sub(band, LEVEL).copy(),
                                 self._sub(band, ODDS).copy(), self._sub(band, GOLD).copy()))
            del self.pending[:-MAX_PENDING]

    def flush(self, force=False):
        """Read and log pending shops once the screen has been quiet for a moment."""
        if not self.pending or (not force and time.time() - self._last_change < QUIET_S
                                and len(self.pending) < MAX_PENDING):
            return
        pending, self.pending = self.pending, []
        if not self.log:  # still track purchases and shop counts, just don't write a file
            for t, round_, names_img, level_img, odds_img, gold_img in pending:
                self._read(t, round_, names_img, level_img, odds_img, gold_img)
            return
        with self.path.open("a", encoding="utf8") as f:
            for t, round_, names_img, level_img, odds_img, gold_img in pending:
                row = self._read(t, round_, names_img, level_img, odds_img, gold_img)
                if row:
                    f.write(json.dumps(row) + "\n")
                    self.logged += 1

    def _read(self, t, round_, names_img, level_img, odds_img, gold_img):
        if round_ and self._game_round and round_.startswith("1-") and self._game_round >= "2-1":
            self.owned, self.last_shop = {}, None  # new game
            self.seen, self.slots = Counter(), Counter()
            self.complete = False
        if round_:
            if self._game_round is None or (round_.startswith("1-") and self._game_round >= "2-1"):
                # first shop of this game we see: from stage 1 / 2-1 we've seen every purchase
                self.complete = round_.startswith("1-") or round_ == "2-1"
            self._game_round = round_
        w = names_img.shape[1]
        shop = [None] * 5
        for k in range(5):  # name sits in the left ~72% of each card (cost on the right)
            text = self._text(names_img[:, int(k * w / 5):int(k * w / 5 + w / 5 * 0.72)])
            shop[k] = self.reader._match(text, self.names_list, cutoff=0.75) if text else None
        if not any(shop):
            return None  # shop hidden (carousel, transition)
        if self.last_shop and not is_new_shop(self.last_shop, shop):
            # same shop: hover/animation redraws, or units bought out of it (slots now empty)
            for i, u in enumerate(self.last_shop):
                if u and shop[i] is None:
                    self.owned[u] = self.owned.get(u, 0) + 1
            self.last_shop = [u if shop[i] else None for i, u in enumerate(self.last_shop)]
            return None
        self.last_shop = shop
        for u in shop:
            if u:
                self.seen[u] += 1
                self.slots[self.champions[u]["cost"]] += 1
        level = re.search(r"(\d+)", self._text(level_img))
        odds = [int(p) for p in re.findall(r"(\d+)\s*%", self._text(odds_img))]
        gold = re.search(r"(\d+)", self._text(gold_img))
        return {
            "t": round(t, 2), "round": round_,
            "level": int(level.group(1)) if level and 1 <= int(level.group(1)) <= 10 else None,
            "odds": odds if len(odds) == 5 else None,
            "gold": int(gold.group(1)) if gold else None,
            "shop": shop,
            "costs": [self.champions[u]["cost"] if u else None for u in shop],
            "owned": {u: n for u, n in self.owned.items() if n},
        }

    def _text(self, img):
        res = self.rec(np.ascontiguousarray(img))
        return " ".join(res.txts) if res.txts else ""
