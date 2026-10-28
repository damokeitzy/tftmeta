"""Turns a game frame into game state (active traits, shop units) using OCR.

All regions are fractions of the game frame so it works at any resolution.
"""
import difflib
import logging
import re

import numpy as np

from .gamedata import load_augment_names, load_item_names

logging.getLogger("RapidOCR").setLevel(logging.WARNING)

# (x0, y0, x1, y1) as fractions of the frame. Generous on purpose: text that
# doesn't fuzzy-match a known name is ignored.
TRAIT_REGION = (0.0, 0.12, 0.22, 0.75)
SHOP_REGION = (0.20, 0.86, 0.85, 1.0)   # also where armory item cards show their names
ROUND_REGION = (0.37, 0.0, 0.45, 0.04)  # "4-2" in the top bar
AUGMENT_REGION = (0.18, 0.46, 0.82, 0.54)  # names on the 3 augment cards
PLAYERS = (0.86, 0.12, 0.985, 0.78)  # player list: names (scout) + HP numbers (econ), one OCR pass

SLASH_LOOKALIKES = "/1lI|"
OCR_THREADS = 2
CHANGED_PX = 30   # a thumbnail pixel moved by more than this (0-255)...
CHANGED_MIN = 4   # ...in at least this many pixels = the region changed; re-read it


def crop(frame, region):
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = region
    return frame[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]


def parse_count(text, breakpoints):
    """'4/6' -> 4. OCR often reads the slash as '1' ('717' = 7/7), so try every
    slash-lookalike as the split point and keep the one whose right side is a
    real breakpoint for this trait."""
    text = re.sub(r"[^0-9/lI|]", "", text)
    if not text:
        return None
    if "/" in text:
        left = re.sub(r"\D", "", text.split("/")[0])
        return int(left) if left else None
    best = None
    for i, ch in enumerate(text):
        if ch not in SLASH_LOOKALIKES or i == 0:
            continue
        left, right = text[:i], re.sub(r"\D", "", text[i + 1:])
        if not left.isdigit() or not right:
            continue
        if int(right) in breakpoints and int(left) <= max(int(right), max(breakpoints)):
            best = int(left)
            break
    if best is None and text[0].isdigit():
        best = int(text[0])
    return best


class Reader:
    def __init__(self, set_data):
        from rapidocr import RapidOCR  # slow import, keep it lazy

        # 2 threads, no text-rotation pass: by default it grabs every core and makes the game stutter
        self.ocr = RapidOCR(params={
            "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Global.use_cls": False,
            "Global.log_level": "warning",
        })
        # recognition only (no text detection), 1 thread: ~10x cheaper for one line of text whose
        # position is known (gold, level, a shop card's name). Separate engine because per-call
        # mode switches stick to an engine.
        self.rec = RapidOCR(params={"Global.use_det": False, "Global.use_cls": False,
                                    "Global.log_level": "warning",
                                    "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                                    "EngineConfig.onnxruntime.inter_op_num_threads": 1})
        self._cache = {}
        self.traits = set_data["traits"]
        self.champions = set_data["champions"]
        self.item_names = load_item_names()
        self.augment_names = load_augment_names()

    def _lines(self, img, key=None):
        """OCR -> list of (x, y, text) using each box's top-left corner.

        With a key, the last result for that screen region is reused while the region
        looks the same — OCR is the expensive part and most regions rarely change."""
        if key:
            hit, thumb = self._unchanged(key, img)
            if hit is not None:
                return hit
        r = self.ocr(np.ascontiguousarray(img))
        lines = [] if r.txts is None else \
            [(float(b[0][0]), float(b[0][1]), t) for b, t in zip(r.boxes, r.txts)]
        if key:
            self._cache[key] = (thumb, lines)
        return lines

    def _unchanged(self, key, img):
        """(cached value or None, thumbnail) — the value is reused while the region looks the same.
        "Unchanged" = almost no pixel moved noticeably; a mean over the region would miss a single
        digit changing ("2" -> "3" in a trait count). Small regions (one number) are compared at
        full resolution: at 1/4, "16" -> "18" XP is only a pixel or two."""
        step = 4 if img.shape[0] >= 120 else 2 if img.shape[0] >= 60 else 1
        thumb = img[::step, ::step].mean(axis=2).astype(np.float32)
        prev = self._cache.get(key)
        if prev and prev[0].shape == thumb.shape and \
                np.count_nonzero(np.abs(prev[0] - thumb) > CHANGED_PX) < CHANGED_MIN:
            return prev[1], thumb
        return None, thumb

    def text(self, img, key=None):
        """One line of text at a known spot (rec-only engine), cached per key."""
        if key:
            hit, thumb = self._unchanged(key, img)
            if hit is not None:
                return hit
        r = self.rec(np.ascontiguousarray(img))
        out = " ".join(r.txts) if r.txts else ""
        if key:
            self._cache[key] = (thumb, out)
        return out

    def boxes(self, img, key=None):
        """Full OCR -> [(box height, x, y, text)], cached per key (the player list rarely changes)."""
        if key:
            hit, thumb = self._unchanged(key, img)
            if hit is not None:
                return hit
        r = self.ocr(np.ascontiguousarray(img))
        out = [] if r.txts is None else \
            [(abs(b[2][1] - b[0][1]), float(b[0][0]), float(b[0][1]), t) for b, t in zip(r.boxes, r.txts)]
        if key:
            self._cache[key] = (thumb, out)
        return out

    @staticmethod
    def _match(text, names, cutoff=0.8):
        m = difflib.get_close_matches(text.strip(), names, n=1, cutoff=cutoff)
        return m[0] if m else None

    def read_traits(self, frame):
        """{'Coven': 7, 'Brawler': 4, ...}

        Two layouts exist: PC shows the count as a lone number left of the name
        ("2 Spellweaver", breakpoints "2 > 4 > 6" below); the mobile-style UI shows
        the name with "4/6" below it."""
        h = frame.shape[0]
        row = h * 0.012  # same-row tolerance in px (the "1/2" line below is ~0.018h away)
        lines = self._lines(crop(frame, TRAIT_REGION), "traits")
        names = list(self.traits)
        found = {}
        for x, y, text in lines:
            trait = self._match(text, names)
            if not trait or trait in found:
                continue
            breakpoints = self.traits[trait] or [1]
            left = [(x - lx, lt) for lx, ly, lt in lines
                    if h * 0.008 < x - lx < h * 0.08 and abs(ly - y) < row
                    and lt.strip().isdecimal() and int(lt.strip()) <= max(breakpoints) + 3]
            if left:
                count = int(min(left)[1].strip())
            else:
                below = [(ly - y, lt) for lx, ly, lt in lines
                         if 0 < ly - y < h * 0.08 and abs(lx - x) < h * 0.04
                         and any(c.isdigit() for c in lt)]
                count = parse_count(min(below)[1], breakpoints) if below else None
            if not count and breakpoints and breakpoints[0] == 1:
                count = 1  # a 1-unit trait (Rival, Bounty Seeker...) may show with no number
            if count:
                found[trait] = count
        return found

    def read_bottom(self, frame):
        """One OCR pass over the bottom strip -> (shop champions, offered item names).

        Offered items come from armory-style pick cards; fewer than 2 matches is treated
        as noise, since those screens always show several cards."""
        champs = list(self.champions)
        shop, offered = [], []
        for _, _, text in self._lines(crop(frame, SHOP_REGION), "bottom"):
            champ = self._match(text, champs, cutoff=0.75)
            if champ:
                shop.append(champ)
                continue
            item = self._match(text, self.item_names, cutoff=0.85)
            if item:
                offered.append(item)
        return shop, (offered if len(offered) >= 2 else [])

    def read_shop(self, frame):
        """Champion names visible in the shop (may repeat)."""
        return self.read_bottom(frame)[0]

    def read_round(self, frame, cropped=False):
        """'4-2' (Tocker's Trials: up to '3-10') or None. cropped=True: `frame` is already just ROUND_REGION
        (cheap polling)."""
        import cv2
        img = frame if cropped else crop(frame, ROUND_REGION)
        # Tocker's wider top bar puts the label right at the crop's left edge, where the text detector
        # misses it (stage 1 was never read): give it a margin
        img = cv2.copyMakeBorder(np.ascontiguousarray(img), 16, 16, 16, 16, cv2.BORDER_REPLICATE)
        for attempt in (img, None):
            if attempt is None:  # label is dimmed on some screens (augment pick): enlarge + boost contrast
                g = cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2GRAY)
                g = cv2.normalize(cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), None, 0, 255,
                                  cv2.NORM_MINMAX)
                attempt = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
                key = "round_boost"
            else:
                key = "round"
            for _, _, text in self._lines(attempt, key):
                m = re.search(r"(\d)\s*[-–]\s*(10|\d)", text)
                if m:
                    return f"{m.group(1)}-{m.group(2)}"
        return None

    def read_augments(self, frame):
        """Augment names on the pick screen, left to right (cards still flipping are skipped)."""
        img = crop(frame, AUGMENT_REGION)
        w = img.shape[1]
        slots = {}
        for x, _, text in self._lines(img, "augments"):
            name = self._match(text, self.augment_names, cutoff=0.82)
            if name:
                slots[min(2, int(x / (w / 3)))] = name
        return [slots[k] for k in sorted(slots)]
