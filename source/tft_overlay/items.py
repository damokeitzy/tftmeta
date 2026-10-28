"""Finds item icons on screen by template matching against Community Dragon icons.

- Bench: the 10 slots down the left edge.
- Units: icons sit just under each of *your* units' health bars (green); enemy bars are red.
Sizes/offsets are fractions of frame height (UI scales with height), measured at 1080p.
"""
import json
from pathlib import Path

import cv2
import numpy as np

from .paths import DATA  # noqa: E402

BENCH_X0, BENCH_Y0, BENCH_STEP, BENCH_SIZE, BENCH_SLOTS = 0.0093, 0.2407, 0.050, 0.0417, 10
UNIT_ICON = 0.0232          # item icon edge under a health bar
UNIT_DX, UNIT_DY = -0.0065, 0.004   # icon row origin relative to bar's left/bottom
BOARD = (0.15, 0.05, 0.85, 0.80)    # where units can stand (x0, y0, x1, y1 fractions)
MATCH_MIN = 0.7   # unit items: real icons score 0.82+, unknown ones (artifacts) ~0.45-0.6
BENCH_MIN = 0.55  # bench slots have their own frame, real icons score lower there
ARTIFACT_MIN = 0.85     # artifacts are rare: an icon we don't have art for lands on one at ~0.7...
ARTIFACT_MARGIN = 0.12  # ...with no clear winner; a real one (even under a stack number) stands out
# carousel: icons float above champions walking a ring in the middle of the screen.
CAROUSEL_REGION = (0.18, 0.08, 0.82, 0.85)
CAROUSEL_SIZES = (0.03,)  # carousel icons are ~32px at 1080p (checked on 6 carousels)
COARSE_MIN, FIND_MIN = 0.6, 0.8  # real icons classify >= 0.82 on test frames


def _inner(img, keep=0.8):
    """Center crop: drops borders and the stack-count digits in icon corners."""
    h, w = img.shape[:2]
    dy, dx = int(h * (1 - keep) / 2), int(w * (1 - keep) / 2)
    return img[dy:h - dy, dx:w - dx]


def _has_dark_frame(gray, cx, cy, size, dark=40):
    """True if a thin ring just outside the icon is mostly near-black (the icon outline).
    Tries a few radii because the coarse size guess can be off by a few px."""
    h, w = gray.shape
    for r in (size * 0.45, size * 0.5, size * 0.55, size * 0.6):
        y0, y1, x0, x1 = int(cy - r), int(cy + r), int(cx - r), int(cx + r)
        if y0 < 0 or x0 < 0 or y1 + 3 > h or x1 + 3 > w:
            continue
        ring = np.concatenate([gray[y0:y0 + 3, x0:x1].ravel(), gray[y1:y1 + 3, x0:x1].ravel(),
                               gray[y0:y1, x0:x0 + 3].ravel(), gray[y0:y1, x1:x1 + 3].ravel()])
        if np.percentile(ring, 30) < dark:
            return True
    return False


class ItemReader:
    def __init__(self):
        self.items = json.loads((DATA / "items.json").read_text(encoding="utf8"))
        # this set's artifacts (from the armory / forge augments) — detected like any item
        special = json.loads((DATA / "special.json").read_text(encoding="utf8"))
        for name, icon in special.items():
            self.items.setdefault(icon.rsplit(".", 1)[0], {"name": name, "components": [], "icon": icon,
                                                           "special": True})
        self.icons = {}
        for api, it in self.items.items():
            path = DATA / "icons" / it["icon"]
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is not None:
                self.icons[api] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Radiant items have their own art but finish the item they upgrade: matched with their own icon,
        # reported as the normal item (a Radiant Deathblade is a finished Deathblade)
        self.base = {}
        radiant = DATA / "radiant.json"
        if radiant.exists():
            for api, it in json.loads(radiant.read_text(encoding="utf8")).items():
                img = cv2.imread(str(DATA / "icons" / it["icon"]), cv2.IMREAD_COLOR)
                if img is not None and it["of"] in self.items:
                    self.icons[api] = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                    self.base[api] = it["of"]
        self._cache = {}

    def _templates(self, size):
        """Icons shrunk to `size` px (inner part only), cached per size."""
        if size not in self._cache:
            self._cache[size] = {
                api: cv2.resize(_inner(img), (size, size), interpolation=cv2.INTER_AREA)
                for api, img in self.icons.items()
            }
        return self._cache[size]

    def _gray_templates(self, size):
        key = ("gray", size)
        if key not in self._cache:
            self._cache[key] = {api: cv2.cvtColor(t, cv2.COLOR_RGB2GRAY)
                                for api, t in self._templates(size).items()}
        return self._cache[key]

    def is_artifact(self, api):
        it = self.items.get(api) or {}
        return bool(it.get("special")) and not it.get("name", "").endswith(" Emblem")

    def classify(self, patch, margin=False):
        """Best item for a single icon-sized patch -> (api, score), or (api, score, lead over the
        next-best item) with margin=True."""
        patch = cv2.resize(patch, (40, 40), interpolation=cv2.INTER_AREA)
        best, second = (None, 0.0), 0.0
        for api, tpl in self._templates(32).items():
            s = float(cv2.matchTemplate(patch, tpl, cv2.TM_CCOEFF_NORMED).max())
            if s > best[1]:
                best, second = (api, s), best[1]
            elif s > second:
                second = s
        best = (self.base.get(best[0], best[0]), best[1])  # Radiant -> the item it upgrades
        return (*best, best[1] - second) if margin else best

    def read_bench(self, frame):
        h = frame.shape[0]
        size, x0 = int(h * BENCH_SIZE), int(h * BENCH_X0)
        out = []
        for k in range(BENCH_SLOTS):
            y0 = int(h * (BENCH_Y0 + k * BENCH_STEP))
            slot = frame[y0:y0 + size, x0:x0 + size]
            if slot.size == 0 or _inner(slot, 0.6).mean() < 30:  # empty slots are black
                continue
            api, score = self.classify(slot)
            if api and score >= BENCH_MIN:
                out.append(api)
        return out

    def _ally_bars(self, frame):
        """(left, bottom) of green health bars on the board."""
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = (int(w * BOARD[0]), int(h * BOARD[1]), int(w * BOARD[2]), int(h * BOARD[3]))
        roi = frame[y0:y1, x0:x1].astype(np.int16)
        r, g, b = roi[..., 0], roi[..., 1], roi[..., 2]
        mask = ((g > 140) & (r < 120) & (b < 120) & (g - r > 60)).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        bars = []
        for i in range(1, n):
            bx, by, bw, bh, _ = stats[i]
            if bw >= h * 0.012 and 2 <= bh <= h * 0.009 and bw > 4 * bh:
                bars.append((x0 + bx, y0 + by + bh))
        # several segments of one bar (notches) -> keep the left-most per row
        bars.sort()
        merged = []
        for bx, bb in bars:
            if not any(abs(bb - mb) < h * 0.006 and 0 <= bx - mx < h * 0.08 for mx, mb in merged):
                merged.append((bx, bb))
        return merged

    def read_units(self, frame):
        """List of item lists, one per ally unit that holds items."""
        h = frame.shape[0]
        size = int(h * UNIT_ICON)
        units = []
        for left, bottom in self._ally_bars(frame):
            ix, iy = int(left + h * UNIT_DX), int(bottom + h * UNIT_DY)
            held = []
            for k in range(3):
                patch = frame[iy:iy + size, ix + k * size:ix + (k + 1) * size]
                if patch.shape[0] < size * 0.8 or patch.shape[1] < size * 0.8:
                    break
                api, score, lead = self.classify(patch, margin=True)
                if not api or score < MATCH_MIN:
                    break  # items are packed left, first miss = no more items
                if self.is_artifact(api) and score < ARTIFACT_MIN and lead < ARTIFACT_MARGIN:
                    continue  # no clear winner: an item we have no art for, not a forge item
                held.append(api)
            if held:
                units.append(held)
        return units

    def find_icons(self, frame, region=CAROUSEL_REGION, sizes=CAROUSEL_SIZES):
        """Item icons anywhere in `region` (carousel: icons float over the champions).

        Two stages so it stays fast: coarse multi-scale search at half resolution to find
        candidate spots, then classify() each spot at full resolution.
        Returns [(api, (x, y), score)] for distinct icons, best first."""
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = (int(w * region[0]), int(h * region[1]), int(w * region[2]), int(h * region[3]))
        roi = frame[y0:y1, x0:x1]
        small = cv2.cvtColor(cv2.resize(roi, (roi.shape[1] // 2, roi.shape[0] // 2),
                                        interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        candidates = []  # (score, cx, cy, size) in full-res frame coords
        for frac in sizes:
            size = int(h * frac)
            half = max(8, int(size * 0.8) // 2)  # inner part of the icon, at half scale
            for api, tpl in self._gray_templates(half).items():
                res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
                for yy, xx in zip(*np.where(res >= COARSE_MIN)):
                    cx = x0 + (xx + half / 2) * 2
                    cy = y0 + (yy + half / 2) * 2
                    candidates.append((float(res[yy, xx]), cx, cy, size))
        candidates.sort(reverse=True)
        found, taken, tried = [], [], set()
        for score, cx, cy, size in candidates[:1500]:
            # icons sit edge to edge, so only suppress spots closer than half the smaller icon
            if any(abs(cx - tx) < min(ts, size) * 0.5 and abs(cy - ty) < min(ts, size) * 0.5
                   for tx, ty, ts in taken):
                continue
            cell = (int(cx // (size * 0.3)), int(cy // (size * 0.3)), size)
            if cell in tried:  # already rejected (nearly) this exact spot
                continue
            tried.add(cell)
            patch = frame[int(cy - size / 2):int(cy + size / 2), int(cx - size / 2):int(cx + size / 2)]
            if patch.shape[0] < size * 0.8 or patch.shape[1] < size * 0.8:
                continue
            if not _has_dark_frame(gray, cx, cy, size):  # UI icons are outlined; terrain isn't
                continue
            api, s = self.classify(patch)
            if api and s >= FIND_MIN:
                taken.append((cx, cy, size))
                found.append((api, (int(cx), int(cy)), s))
        return found

    def name(self, api):
        return self.items[api]["name"]
