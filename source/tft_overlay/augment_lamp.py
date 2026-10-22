"""The augments you took, read off the little dark box above the lamp on the left of your board: one icon
per augment (silver / gold / prismatic), ~37px apart at 1080p, the box centred over the lamp and growing
as you take more.

After an augment screen the app knows the three offers; the one that then shows up in the box is the one
you took. Matching an icon against three candidates is reliable (colour template match, ~0.8+ for the right
one), and an opponent's box can't be mistaken for yours: it would have to show one of your offers.
Icons: data/augment_icons (tools/fetch_augment_icons.py).
"""
import json

import cv2
import numpy as np
from PIL import Image

from .gamedata import DATA

BOX = (0.2188, 0.2093, 0.3177, 0.2481)  # where the box can be (fractions of the frame), measured at 1920x1080
PITCH = 37.7 / 1080                     # one icon's width, as a fraction of the frame height
DARK = 50                               # the box's background: every channel below this
MIN_SCORE = 0.6                         # an icon at least this much like an offer...
MIN_MARGIN = 0.05                       # ...clearly more like it than the other offers...
BEST_OF_ALL = 0.02                      # ...and (within this) the best match of all the set's augment icons


class AugmentLamp:
    def __init__(self):
        path = DATA / "augment_icons.json"
        self.files = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
        self._raw, self._sized = {}, {}

    def _tpl(self, name, size):
        """The augment's icon on the box's dark background, size x size, RGB float."""
        key = (name, size)
        if key not in self._sized:
            if name not in self._raw:
                im = Image.open(DATA / "augment_icons" / self.files[name]).convert("RGBA")
                bg = Image.new("RGBA", im.size, (24, 24, 26, 255))
                bg.alpha_composite(im)
                self._raw[name] = bg.convert("RGB")
            self._sized[key] = np.asarray(self._raw[name].resize((size, size), Image.LANCZOS), dtype=np.float32)
        return self._sized[key]

    def icons(self, frame):
        """The icons in the box, left to right (RGB crops), or [] when there's no box (no augments yet,
        another screen)."""
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = int(BOX[0] * w), int(BOX[1] * h), int(BOX[2] * w), int(BOX[3] * h)
        dark = frame[y0:y1, x0:x1].max(axis=2) < DARK
        cols = np.where(dark.mean(axis=0) > 0.35)[0]
        rows = np.where(dark.mean(axis=1) > 0.25)[0]
        if len(cols) < 10 or len(rows) < 10:
            return []
        left, right, top, bottom = x0 + cols.min(), x0 + cols.max() + 1, y0 + rows.min(), y0 + rows.max() + 1
        n = max(1, round((right - left) / (PITCH * h)))
        return [frame[top:bottom, int(left + k * (right - left) / n):int(left + (k + 1) * (right - left) / n)]
                for k in range(n)]

    def score(self, crop, name):
        """How much this icon looks like the augment's (0..1; a few sizes, the icon sits inside its slot)."""
        if name not in self.files:
            return 0.0
        c = np.ascontiguousarray(crop, dtype=np.float32)
        side = min(c.shape[:2])
        return max(float(cv2.matchTemplate(c, self._tpl(name, int(side * f)), cv2.TM_CCOEFF_NORMED).max())
                   for f in (0.78, 0.86, 0.94))

    def taken(self, frame, offers, known=()):
        """Which of `offers` is now in your box (and isn't one of `known`), or None."""
        offers = [o for o in offers if o in self.files and o not in known]
        if not offers:
            return None
        for crop in reversed(self.icons(frame)):  # newest (rightmost) first
            if min(crop.shape[:2]) < 12:
                continue
            ranked = sorted(((self.score(crop, o), o) for o in offers), reverse=True)
            best, name = ranked[0]
            runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
            if best < MIN_SCORE or best - runner_up < MIN_MARGIN:
                continue
            # an older augment can look a bit like an offer (Replication vs Healing Orbs: 0.64): the offer
            # must be what this icon looks most like of every augment (same-art variants tie)
            if best >= max(self.score(crop, n) for n in self.files) - BEST_OF_ALL:
                return name
        return None
