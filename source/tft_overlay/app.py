"""Always-on-top overlay showing the best-fitting meta comps for the current board.

Layout (fixed size, never resizes):
  title bar      – drag to move, Build ▾ menu (lock a comp / Auto), pause, close
  item panel     – animated icons: carry targets + components to get, or, on carousel /
                   armory rounds, the best items to grab ranked for your build
  summary        – traits read, bench, built items
  comp cards     – click one to lock it as your build
"""
import argparse
from collections import Counter, deque
import ctypes
import json
import math
import queue
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageTk

from .capture import Capturer
from .gamedata import DATA, OTHER_GUIDES, STATS_FILE, load_comps, load_set, load_special
from .board_draw import board_height, draw_hex_board
from .augment_lamp import AugmentLamp
from .history import GameLog
from .augments import rank_augments
from .hud import shift
from .econ import GOLD as GOLD_REGION, LVL as LVL_REGION, advise, read_econ
from .plans import make_plan
from .guide import board_order, key_traits
from .items import ItemReader
from .matcher import needs_text, score as score_comp, strength, suggest, unmet
from .reader import AUGMENT_REGION, ROUND_REGION, TRAIT_REGION, Reader
from .roster import is_reroll, missing_carries, on_board, why_missing
from .scout import Scout
from .shoplog import BAND as SHOP_BAND, ShopLogger
from .slams import ItemBook, fill, main_tank, rank_picks, split_held, unit_partials
from .ui_common import (ACCENT, BG, CARD, COST_COLOR, DIM, FG, GREEN, ROOT, TIER_COLOR, IconCache, tier_rank,
                        clamp_to_screen, load_config, mix, monitor_rect, save_config)

PANEL_H = 84
GENERAL_PLAN = make_plan("", 4)  # no build pinned: standard leveling (4 at 2-1 ... 8 at 4-1) and econ


def is_carousel(round_):
    return bool(round_) and (round_ == "1-1" or re.fullmatch(r"\d-4", round_) is not None)


class FileSource:
    """Demo mode: serve a saved screenshot instead of the live game."""

    def __init__(self, path):
        self.frame = __import__("numpy").array(Image.open(path).convert("RGB"))

    def grab(self, region=None):
        if not region:
            return self.frame
        h, w = self.frame.shape[:2]
        return self.frame[int(h * region[1]):int(h * region[3]), int(w * region[0]):int(w * region[2])]


def guide_now(comp, round_):
    """The guide's 'how to play' for where you are: this stage's line ('Stage 3: save HP, roll down on
    4-2 for Ahri 2 and Morgana 2'), its overall gameplan before stage 3, item holders, emblem, tips."""
    h = comp.get("howto") or {}
    stage = int(round_[0]) if round_ and round_[0].isdigit() else None
    # only the stage you're on (no game yet: the first one), so it swaps as the game moves on
    now = stage_for(h.get("stages"), stage or 1) or (None if stage else next(iter(h.get("stages") or []), None))
    if not (now or h.get("tips") or h.get("holders")):
        return None
    return {"stage": now, "gameplan": h.get("gameplan") if stage is None or stage <= 2 else "",
            "holders": h.get("holders") or "", "emblem": h.get("emblem") or "",
            "tips": h.get("tips") or [], "items": h.get("items") or [],
            "by_stage": h.get("stages") or []}  # for the leveling chips: each step's stage line


def stage_for(stages, n):
    """The guide's [label, text] for stage n ('Stage 1-2', 'Stage 3', 'Stage 5+'), or None."""
    found = None
    for label, text in stages or []:
        nums = [int(x) for x in re.findall(r"\d", label)]
        if n and nums and (nums[0] <= n <= nums[-1] or (label.rstrip().endswith("+") and n >= nums[0])):
            found = [label, text]
    return found


# what a read looks like with no game on screen (a build picked from the menu between games)
NO_GAME = {"round": None, "traits": {}, "shop": [], "bench": [], "held": [], "held_units": [], "phase": None,
           "offers": [], "econ": {}, "slots": []}


class Worker(threading.Thread):
    """Captures + reads on a background thread so the UI never blocks."""

    def __init__(self, cfg, out, source=None):
        super().__init__(daemon=True)
        self.cfg, self.out, self.source = cfg, out, source
        self.paused = threading.Event()
        self.refresh = threading.Event()  # ⟳: full read now
        self.relock = threading.Event()   # build changed: re-advise from the last read
        self.report = threading.Event()   # ⚑: save the screen + what was read, for fixing misreads
        self.manual = set()   # units you said you have ("I have Kha'Zix"): orbs/carousel aren't purchases
        self.manual_items = set()  # items you said a champion has (click a slot): misread items
        self._items_ok = Counter()    # finished items seen in two reads in a row this game
        self._items_last = Counter()  # finished items in the last read
        self.locked = cfg.get("locked")  # comp name the user pinned, or None for auto
        self._top = None           # unpinned: the build on the board at the last read, and its round
        self._top_round = None
        self._buy_ok = None        # unpinned: shop units worth a BUY flash (in 2+ of the 3 builds on offer)
        self._buy_units = []       # what the popup over the game's shop may show (pinned build / safe buys)
        self._buy_cap = {}         # unit -> copies worth buying (9 if the guide has it 3★; default 3 for a 2★)
        self._offered_25 = False   # the 2-5 "core carry missing" pivot was offered this game
        self._offer = None         # ((build, why, auto, missing units), expires after round)
        self.augments = []         # augments you took this game (read off the box above your lamp)
        self.aug_history = []      # [{"round", "offered", "took"}] this game, for the game history
        self.history = GameLog()   # this game as it goes -> data/games/history.jsonl
        self._aug_offers = (None, [])  # (augment round, every offer seen on that screen) until one is taken
        self._hp_start = {}        # round -> your HP at its first read (before its combat), this game
        self._hp_ok = None         # last HP read that made sense, and a jump waiting for a second read
        self._hp_odd = None

    def dismiss_offer(self):
        """'Keep my build': drop the pivot offer."""
        self._offer = None
        self.relock.set()

    def mark_item(self, api):
        """Clicked an item slot: you already have this item (the reader missed it) — or click again
        to take that back."""
        self.manual_items ^= {api}
        self.relock.set()

    def have(self, unit):
        """You said you hold this unit: it stops counting as missing; if auto mode switched away
        because of it, go back."""
        self.manual.add(unit)
        self.relock.set()

    def run(self):
        set_data = load_set()
        comps = load_comps()
        reader = Reader(set_data)
        items = ItemReader()
        book = ItemBook(items.items)
        special = load_special()
        cap = self.source or Capturer()
        lamp = AugmentLamp()
        last_round, schedule, last_full = None, [], 0.0
        gone_since = None  # when the TFT window disappeared (the game history closes the game after 2 min)
        previewed = None  # no game running: the pinned build last shown (menu pick between games)
        reading = None  # last full read: dict of everything seen on screen
        carousel_reads = deque(maxlen=2)  # carousel: what's still there (last 2 reads)
        next_carousel = 0.0
        saved_rounds = set()
        # shop logger: every shop incl. roll-down rerolls, for tools/shop_report.py (live only)
        # shop reader: quick read on every shop change (board flash) + purchases/shop counts this
        # game; it also writes the shop log (tools/shop_report.py) when live and enabled
        shoplog = ShopLogger(reader, set_data["champions"],
                             log=self.cfg.get("shop_log", True) and not self.source)
        tick = 0.4
        # opponent tracking: notices when you scout someone and stores their traits
        scout = Scout(reader, comps, set_data["champions"], self.cfg.get("player_name")) \
            if not self.cfg.get("log_only") else None

        shown_comp = [None]  # comp on the board right now
        watch_last = [None]  # thumbnails of the watched regions at the last full read

        def watch_thumb():
            """Traits panel (+ item bench), augment cards, gold and level, small: when any of them
            changes you bought / placed / slammed / rerolled something, so read again now."""
            parts = []
            for reg in (TRAIT_REGION, AUGMENT_REGION, shift(GOLD_REGION), shift(LVL_REGION)):
                img = cap.grab(reg)
                if img is None:
                    return None
                step = 4 if img.shape[0] >= 120 else 2 if img.shape[0] >= 60 else 1
                parts.append(img[::step, ::step].mean(axis=2).astype(np.float32))
            return parts

        def watch_changed(a, b):
            return any(x.shape != y.shape or np.count_nonzero(np.abs(x - y) > 30) >= 6 for x, y in zip(a, b))

        def suggest_for(traits):
            return suggest(comps, traits, [], set_data["champions"])

        def icon_for(label):
            if label in book.items:
                return book.items[label]["icon"]
            if label.lower() in book.by_name:
                return book.items[book.by_name[label.lower()]]["icon"]
            return special.get(label)

        def carousel_items(frame):
            """Item icons on the carousel right now. Icons under your own units' health bars
            (as they walk back onto the board) aren't offers."""
            h = frame.shape[0]
            bars = items._ally_bars(frame)
            return [api for api, (x, y), _ in items.find_icons(frame)
                    if not any(bx - h * 0.015 <= x <= bx + h * 0.09 and by - h * 0.01 <= y <= by + h * 0.04
                               for bx, by in bars)]

        def carousel_offers():
            """Items in the latest read. The read before only counts when the latest one lost several
            at once (champions walking over the icons), so a taken item drops within ~1.5s."""
            if not carousel_reads:
                return []
            latest = carousel_reads[-1]
            prev = carousel_reads[0] if len(carousel_reads) > 1 else []
            if len(latest) < len(prev) - 2:
                return list(dict.fromkeys(list(latest) + list(prev)))
            return list(dict.fromkeys(latest))

        def full_read(frame, round_):
            who = scout.learn(frame) if scout else None  # lobby names + which board label is you
            traits = reader.read_traits(frame)
            if who and reading and traits == reading.get("traits"):
                # "their" board has exactly your traits: it's yours, with your label hidden (augment
                # screen, combat). Never store your own board as an opponent's
                who = scout.on_opponent = None
            if who and reading:
                # you're looking at an opponent's board: their traits go to the scout, your own
                # board/bench/items stay as last read
                scout.record(who, traits, round_)
                print(f"round={round_} scouting {who}: {scout.opponents.get(who)}", flush=True)
                return dict(reading, round=round_)
            if scout:
                scout.mine = traits
            h, w = frame.shape[:2]
            x0, y0, x1, y1 = SHOP_BAND
            slots = shoplog.names(frame[int(h * y0):int(h * y1), int(w * x0):int(w * x1)])  # 5 cards, None = empty
            quick = [u for u in slots if u]
            if is_carousel(round_) and not quick:  # no shop or armory during a carousel
                shop, offered = [], []
            elif len(quick) >= 2:  # shop is up: no armory cards over it, skip the full bottom OCR
                shop, offered = quick, []
            else:
                shop, offered = reader.read_bottom(frame)
            bench = sorted(items.read_bench(frame))
            held_units = items.read_units(frame)  # per unit: components on a unit matter
            held = sorted(a for unit in held_units for a in unit)
            # finished items only get added during a game (a sold unit drops them on the bench), so
            # one seen in two reads in a row is remembered: a missed read never "takes it away"
            cur = Counter(a for a in held + list(bench) if book.is_built(a))
            for a, n in cur.items():
                self._items_ok[a] = max(self._items_ok[a], min(n, self._items_last[a]))
            self._items_last = cur
            phase, offers = None, []
            augs = reader.read_augments(frame) if round_ in ("2-1", "3-2", "4-2") or not round_ else []
            if augs and (len(augs) >= 2 or round_ in ("2-1", "3-2", "4-2")):  # augment pick screen
                phase, offers = "augment", augs
            elif offered:  # armory-style pick cards
                phase = "armory"
                offers = [book.by_name.get(n.lower(), n) for n in offered]
            elif self.cfg.get("force_carousel") or (is_carousel(round_) and not shop):
                phase = "carousel"
                # your units are off the board during a carousel, and the carousel champions carry
                # items of their own: keep the items your units had at the last real read
                if reading:
                    held_units = reading["held_units"]
                    held = sorted(a for unit in held_units for a in unit)
                carousel_reads.append(carousel_items(frame))
                offers = carousel_offers()
                if self.cfg.get("save_carousel_frames") and round_ not in saved_rounds and not self.source:
                    saved_rounds.add(round_)  # one frame per carousel, for calibrating icon sizes
                    (ROOT / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
                    Image.fromarray(frame).save(ROOT / "tests" / "fixtures" / f"carousel_{round_}.png")
            # the augment you took: every offer seen on this augment screen (rerolls too) until one of
            # them shows up in the box above your lamp
            if phase == "augment":
                if self._aug_offers[0] != round_:
                    self._aug_offers = (round_, [])
                    self.aug_history.append({"round": round_, "offered": [], "took": None})
                self._aug_offers[1].extend(a for a in offers if a not in self._aug_offers[1])
                self.aug_history[-1]["offered"] = list(self._aug_offers[1])
            elif phase is None and self._aug_offers[1]:
                took = lamp.taken(frame, self._aug_offers[1], self.augments)
                if took:
                    self.augments.append(took)
                    if self.aug_history:
                        self.aug_history[-1]["took"] = took
                    self._aug_offers = (None, [])
                    print(f"round={round_} augment taken: {took} (all: {self.augments})", flush=True)
            print(f"round={round_} phase={phase} offers={offers} traits={traits} shop={shop} "
                  f"bench={bench} held={held}", flush=True)  # econ printed in publish
            # level, XP, gold, streak, HP (not during a carousel: nothing to spend on, keep it fast)
            econ = reading.get("econ", {}) if phase == "carousel" and reading else read_econ(reader, frame)
            return {"round": round_, "traits": traits, "shop": shop, "bench": bench, "held": held,
                    "held_units": held_units, "phase": phase, "offers": offers, "econ": econ,
                    "slots": slots if len(quick) >= 2 and not phase else []}

        def have_tokens(apis):
            """What your items (or the armory) give toward builds' special needs."""
            out = set()
            for a in apis:
                if a not in book.items:
                    continue
                name = book.name(a)
                if name.endswith(" Emblem"):
                    out.add(f"emblem:{name[:-len(' Emblem')]}")
                elif "Spatula" in a:
                    out.add("spatula")
                elif "FryingPan" in a:
                    out.add("pan")
                elif book.is_special(a):
                    out.add("forge")
            return out

        def aug_tokens(name):
            """What taking this augment would give toward builds' special needs."""
            from .augments import descriptions
            d = (descriptions().get(name) or "").lower()
            out = {f"aug:{name}"}
            if "artifact" in d:
                out.add("forge")
            if "spatula" in d and "gain" in d:
                out.add("spatula")
            for t in set_data["traits"]:
                if f"{t.lower()} emblem" in d:
                    out.add(f"emblem:{t}")
            return out

        def uses(comp):
            held = [u for u in comp["units"] if (shoplog.owned or {}).get(u) or u in self.manual] \
                if not self.source else []
            return f" — uses your {', '.join(held[:4])}" if held else " — build around the units you hold"

        def reroll_progress(comp, bought):
            """[(unit, copies bought)] for a reroll comp's 3★ targets, else []: its main carry plus the one
            other 3★ unit you're furthest along on. Guides mark up to five 3★ units; chasing them all
            (45 copies) locks your bench and your levels."""
            if bought is None or not is_reroll(comp):
                return []
            champs = set_data["champions"]
            cheap = [u for u in comp.get("carries", {}) if u in champs and champs[u]["cost"] <= 3]
            stars = [b["unit"] for b in comp.get("board") or [] if b.get("three") and b["unit"] in champs
                     and champs[b["unit"]]["cost"] <= 3] or cheap
            if not stars:
                return []
            main = comp.get("main") if comp.get("main") in stars else stars[0]
            rest = [u for u in stars if u != main]
            second = max(rest, key=lambda u: (bought.get(u, 0), -rest.index(u))) if rest else None
            return [(u, bought.get(u, 0)) for u in (main, second) if u]

        def still_needed(u):
            """Whether another copy helps: up to 9 for units the pinned build's guide has at 3★, else 3 for a
            2★ — past that a copy just fills the bench. Counts what you've bought."""
            have = max((shoplog.owned or {}).get(u, 0), 1 if u in self.manual else 0)
            return have < self._buy_cap.get(u, 3)

        def buys_now(shop):
            """The shop units to flash BUY: on a pinned build's board, else only safe buys — and only while
            you still need copies."""
            return [u for u in shop if (self._buy_ok is None or u in self._buy_ok) and still_needed(u)]

        def popup_units():
            return [u for u in self._buy_units if still_needed(u)]

        def bench_priority(comp, bought):
            """What to hold for a reroll build, most important first: the 3★ targets (main carry + one, 9
            copies each), then the rest of its board's cheap units (3 copies: 2★). Anything else on your
            bench is the first to sell — holding copies of every unit fills it. '' for other builds."""
            if not is_reroll(comp):
                return ""
            champs = set_data["champions"]
            targets = reroll_progress(comp, bought or {})
            names = [u for u, _ in targets]
            rest = [u for u in board_order(comp, set_data) if u not in names and champs.get(u, {}).get("cost", 9) <= 3]
            return " · ".join([f"{u} {min(n, 9)}/9" for u, n in targets] + [f"{u} ×3" for u in rest[:4]])

        def plan_now(comp):
            """The comp's plan; for reroll comps, levels past the roll level wait for the 3★s."""
            plan = comp.get("plan")
            if not plan or not is_reroll(comp):
                return plan
            m = re.search(r"(\d+)", plan.get("roll_at", ""))
            rl = int(m.group(1)) if m else None
            steps = []
            for stage, what in plan.get("leveling") or []:
                lv = re.search(r"Lv\s*(\d+)", what)
                if rl and lv and int(lv.group(1)) > rl:
                    what = f"{what} · only once 3★ done"
                steps.append([stage, what])
            return dict(plan, leveling=steps)

        def held_of(c, r, bought):
            """The build's champions you have: bought this game, said so, or (maybe) on your board."""
            return [u for u in c["units"] if (bought or {}).get(u) or u in self.manual
                    or on_board(u, r["traits"], set_data["champions"])]

        def builds_mtime():
            return [p.stat().st_mtime if p.exists() else 0
                    for p in [DATA / "comps.json"] + [DATA / f for f in OTHER_GUIDES] + [DATA / STATS_FILE]
                    + [DATA / "comp_overrides.json", DATA / "custom_comps.json"]]  # your edits apply live too

        comps_mtime = [builds_mtime()]

        def publish(r, preview=False):
            """preview: no game running — just the pinned build (board, items, whole guide), nothing from the
            last game (purchases, augments, items, HP) and no offers / scouting."""
            # builds updated on disk (startup sync): reload them
            m = builds_mtime()
            if m != comps_mtime[0]:
                comps_mtime[0] = m
                comps[:] = load_comps()
            # purchases this game, plus units you said you have. "Not bought" only means "don't have"
            # when the overlay saw the game from stage 1 — after a restart, only traits/your word count
            bought = None
            if not self.source and not preview and (shoplog.complete or self.manual):
                bought = dict(shoplog.owned, **{u: max(1, shoplog.owned.get(u, 0)) for u in self.manual})
            pool = (shoplog.seen, shoplog.slots) if not self.source and not preview else None
            have_now = have_tokens([x for u in r["held_units"] for x in u] + list(r["bench"]) +
                                   (list(r["offers"]) if r["phase"] == "armory" else []))
            for a in [] if preview else self.augments:  # augments you took: Consuming Flora builds unlock, emblem augments count
                have_now |= aug_tokens(a)
            # for bunnymuffins' conditions: champions you have, how many finished items, gold, bows
            req_units = {t[5:] for c in comps for t in c.get("guide_req", []) if t.startswith("unit:")}
            have_now |= {f"unit:{u}" for u in req_units if (bought or {}).get(u) or u in self.manual
                         or on_board(u, r["traits"], set_data["champions"])}
            all_items = [x for u in r["held_units"] for x in u] + list(r["bench"])
            # finished items seen in two reads in a row (they don't go away): one missed or extra read
            # doesn't flip "lots of items" builds between A and B
            n_built = 0 if preview else \
                sum(self._items_ok.values()) + sum(1 for a in self.manual_items if not self._items_ok[a])
            if self.source:  # a single screenshot: only one read
                n_built = max(n_built, sum(book.is_built(a) for a in all_items if a in book.items))
            have_now |= {f"items:{k}" for k in range(1, n_built + 1)}
            if ((r.get("econ") or {}).get("gold") or 0) >= 50:
                have_now.add("gold:50")
            n_bows = sum(a == "TFT_Item_RecurveBow" for a in all_items)
            have_now |= {f"bows:{k}" for k in range(1, n_bows + 1)}
            results = suggest(comps, r["traits"], r["shop"], set_data["champions"],
                              bench=r["bench"], held=r["held_units"], book=book, top=None,
                              bought=bought, round_=r["round"], pool=pool, have=have_now)
            econ = r.get("econ") or {}
            # an HP read that jumps (51 -> 3 -> 51: a misread) only counts once a second read agrees; a
            # fight takes at most ~35, healing adds a little
            raw = hp = econ.get("hp")
            if raw is not None and self._hp_ok is not None and not (-20 <= self._hp_ok - raw <= 35):
                if raw == self._hp_odd:  # the second read agrees: it's real
                    self._hp_odd = None
                else:
                    self._hp_odd, hp = raw, self._hp_ok  # keep the last good value until the next read
            elif raw is not None:
                self._hp_odd = None
            if hp is not None:
                self._hp_ok = hp
            econ = dict(econ, hp=hp)
            # HP the last combat cost: HP at the start of this round vs the start of the one before (a
            # round's first read comes before its fight). Over 50 in one round is a misread, not a loss
            rnd = r["round"]
            if rnd and econ.get("hp") is not None and rnd not in self._hp_start:
                self._hp_start[rnd] = econ["hp"]
            prev = max((k for k in self._hp_start if k < rnd), default=None) if rnd in self._hp_start else None
            lost = self._hp_start[prev] - self._hp_start[rnd] if prev else 0
            if (econ.get("streak") or 0) > 0:
                lost = 0  # on a win streak you won the last fight: whatever the HP read says, nothing was lost
            econ = dict(econ, hp_lost=lost if 0 < lost <= 50 else 0)
            # the game history: this read of your board, your augments, the build you're on
            if not self.source and results:
                b = results[0]["comp"]
                self.history.update(rnd, hp=hp, level=econ.get("level"), gold=econ.get("gold"),
                                    build=b.get("guide_name") or b["name"], pinned=bool(self.locked),
                                    traits=r["traits"], owned=dict(shoplog.owned or {}), augments=self.aug_history)
            # 1. you pick: until you pin a build (never automatically) the board shows the top candidate and
            #    the side panel the top 3, re-ranked every read as your units, items and emblems change
            locked = self.locked
            if locked:
                results.sort(key=lambda x: x["comp"]["name"] != locked)  # stable: pinned first
            elif results and self._top in [x["comp"]["name"] for x in results[:3]]:
                # the board doesn't chop and change (buying for three builds = no synergy): within a round
                # the build shown never changes; at a new round only when another of the same tier fits
                # clearly better (15%+), or a higher tier passes it
                prev = next(x for x in results[:3] if x["comp"]["name"] == self._top)
                same_round = self._top_round == r["round"]
                if results[0] is not prev and (same_round or (
                        results[0].get("tier_now") == prev.get("tier_now") and results[0]["score"] < prev["score"] * 1.15)):
                    results.remove(prev)
                    results.insert(0, prev)
            if results and not locked:
                self._top, self._top_round = results[0]["comp"]["name"], r["round"]
            # unpinned, a shop unit only flashes BUY when it's in 2+ of the 3 builds on offer: a safe buy
            # whichever way you go. Pinned: your build's units
            self._buy_ok = None if locked else {
                u for u, n in Counter(u for x in results[:3] for u in set(x["comp"]["units"])).items() if n >= 2}
            # the popup over the game's shop: your pinned build's units, else the safe buys
            self._buy_units = sorted(set(results[0]["comp"]["units"]) if locked and results else self._buy_ok or ())
            # copies worth buying: 9 for the units the pinned build's guide has at 3★, 3 (a 2★) for everything
            # else — no point holding a third copy of anything more
            self._buy_cap = {b["unit"]: 9 for b in results[0]["comp"].get("board") or [] if b.get("three")} \
                if locked and results else {}
            # 2. after that nothing changes except the live bits — and two decision points, each offered
            #    once and kept up until you decide: 2-5 with the build's core carry missing, and an
            #    augment that unlocks a better build you already have champions for
            if self._offer and (self._offer[0][0] == locked or (r["round"] or "") > self._offer[1]):
                self._offer = None  # you took it, or the moment has passed
            mine = results[0] if locked and results else None
            if mine and not self._offered_25 and (r["round"] or "") >= "2-5" and mine.get("missing_core"):
                self._offered_25 = True
                alt = next((x for x in results[1:] if not x.get("missing_core")
                            and len(held_of(x["comp"], r, bought)) >= 3), None)
                if alt:
                    self._offer = ((alt["comp"]["name"], why_missing(mine["missing_core"], r["round"], pool,
                                                                     set_data["champions"]) + uses(alt["comp"]),
                                    False, mine["missing_core"]), "3-2")
            self._aug_unlock = None
            if r["phase"] == "augment" and mine:
                best_aug = None
                for a in r["offers"]:
                    toks = aug_tokens(a)
                    for x in results[1:]:
                        c2 = x["comp"]
                        if not set(unmet(c2, have_now)) - set(unmet(c2, have_now | toks)):
                            continue  # this augment doesn't unlock anything for it
                        mine2 = held_of(c2, r, bought)
                        if len(mine2) < 2:
                            continue  # you need a few of its champions already
                        s2 = score_comp(c2, r["traits"], r["shop"], set_data["champions"], bought, have_now | toks)[0]
                        if s2 > mine["score"] and (best_aug is None or s2 > best_aug[2]):
                            best_aug = (c2["name"], a, s2, mine2)
                if best_aug:
                    self._offer = ((best_aug[0], f"take {best_aug[1]}: it unlocks this build (uses your "
                                                 f"{', '.join(best_aug[3][:3])})", False), r["round"])
                    self._aug_unlock = (best_aug[1], best_aug[0])
            pivot = self._offer[0] if self._offer and not preview else None
            mine_comp = results[0]["comp"] if results else None
            contest = scout.contest(mine_comp, suggest_for) if scout and mine_comp and not preview else []
            lobby = scout.lobby_summary(suggest_for) if scout and not preview else []
            results = results[:3]
            comp = results[0]["comp"]
            # no stand-ins: the guide's champions as listed. Carries you don't have are only skipped
            # for carousel / armory picks
            icomp = dict(comp, _missing=missing_carries(comp, r["traits"], bought, set_data["champions"]))
            shown = {}
            bench, held = r["bench"], r["held_units"]
            seen_now = Counter(a for u in held for a in u if book.is_built(a)) +                 Counter(a for a in bench if book.is_built(a))
            missed = Counter() if preview else self._items_ok - seen_now  # remembered, but this read didn't see them
            if (missed or self.manual_items) and not preview:  # ...and items you clicked as owned
                held = list(held) + [[a] for a in missed.elements()] +                     [[a] for a in self.manual_items if not seen_now[a]]
            # the guide's layout, never moved: its carries in its order, each slot filled with what
            # that champion really holds; only ticks and flashing change
            slots, item_next, next_kind = fill(comp, bench, held, book)
            # tank first means: finish an item already started on the tank, or give a bare tank its
            # first item. Otherwise the carries come first — finishing carry items is never held back
            tank = main_tank(comp, book)
            tank_items = sum(sl[0] in ("done", "covered", "full") for sl in slots.get(tank, []))
            tank_partial = bool(tank) and bool(unit_partials(comp, split_held(held)[0], book).get(tank))
            if tank and tank_items >= 1 and not tank_partial:
                slots, item_next, next_kind = fill(dict(comp, _tank_stable=True), bench, held, book)  # same layout
                icomp = dict(icomp, _tank_stable=True)
            wears = icomp.get("_for", {})
            # carousel / armory picks: never for a carry you don't have
            mine_c = {u: v for u, v in icomp["carries"].items() if u not in icomp.get("_missing", ())
                      and not (icomp.get("_main_missing") and u == next(iter(icomp["carries"])))}
            pick_comp = dict(icomp, carries=mine_c) if mine_c else icomp
            panel = {"phase": r["phase"], "comp": comp["name"]}
            ranked = []
            if r["phase"] == "augment":
                unlock = getattr(self, "_aug_unlock", None)
                panel["picks"] = sorted([(a, 99, f"unlocks {unlock[1].replace(' — Standard', '')}", None, None, None)
                                         if unlock and a == unlock[0] else (a, sc, why, None, None, None)
                                         for a, sc, why in rank_augments(r["offers"], comp, set_data["champions"])],
                                        key=lambda p: -p[1])
            elif r["phase"]:  # carousel / armory: builds stay on screen, what's offered flashes in them
                ranked = rank_picks(r["offers"], pick_comp, bench, held, book, set_data["champions"])
                # flash only the best two (gold, then green): with a full carousel, everything flashing
                # would say nothing
                good = [book.by_name.get(lab.lower(), lab) for lab, sc, _, _ in ranked if sc >= 30]
                good = [a for a in good if a in book.items][:2]
                panel["offers"], panel["best"] = good, (good[0] if good else None)
            ic = lambda a: book.items[a]["icon"] if a and a in book.items else None  # noqa: E731
            spare = {a for a in bench if book.is_component(a)}

            # bottom panel = the guide's builds exactly as listed (items + their components). It never
            # changes; what your champions hold is on the board.
            panel["stars"] = comp.get("stars") or []
            must = comp.get("must") or {}  # the guide's must-have items (bunnymuffins' bold): an asterisk
            panel["targets"] = [(unit, [(ic(sl[1]), book.short(sl[1]), [ic(x) for x in book.recipe(sl[1])],
                                         (book.items.get(sl[1]) or {}).get("name") in must.get(unit, ()))
                                        for sl in lst]) for unit, lst in list(slots.items())[:4]]
            # carousel / armory: where the best piece goes, so just that component (or item) flashes
            def locate(a, unit):
                """[carry, slot, component index or None] where this piece goes in the builds."""
                order = [unit] + [u for u in slots if u != unit] if unit in slots else list(slots)
                for u in order:
                    for k, sl in enumerate(slots[u]):
                        if sl[1] == a and not sl[2]:
                            return [u, k, None]
                for u in order:
                    for k, sl in enumerate(slots[u]):
                        if sl[0] in ("open", "slam") and a in book.recipe(sl[1]):
                            return [u, k, book.recipe(sl[1]).index(a)]
                return None
            panel["flash"] = None
            if r["phase"] in ("carousel", "armory") and panel.get("best"):
                a = panel["best"]
                unit = next((u for lab, sc, w, u in ranked if book.by_name.get(lab.lower(), lab) == a), None)
                panel["flash"] = locate(a, wears.get(unit, unit))
            panel.pop("offers", None)
            panel.pop("best", None)
            item_fill = {unit: [(sl[0], ic(sl[2])) for sl in lst] for unit, lst in slots.items()}
            item_text = "  ".join(filter(None, [
                ("Bench: " + ", ".join(book.short(a) for a in bench)) if bench else "",
                ("Built: " + ", ".join(book.short(a) for a in r["held"] if book.is_built(a))) if r["held"] else "",
            ]))
            champs = set_data["champions"]
            # stages 2-3: the build's early opener (TFT Academy) as one line above the board; the board itself
            # always stays the final one — it's the champ guide
            opener = [e for e in comp.get("early") or [] if e["unit"] in champs] \
                if (r["round"] or "9") < "4-1" else []
            board = {  # the comp's board (as in the guide) + live progress: shop hits, trait counts
                "comp": comp["name"], "title": comp.get("guide_name") or comp["name"], "badge": comp.get("badge"),
                "tier": results[0].get("tier_now") or comp.get("tier"),
                "later": [u for u in comp["units"] if champs.get(u, {}).get("cost", 1) >= 4]
                if (r["round"] or "9") < "4-1" else [],
                "units": [(u, champs[u]["icon"], champs[u]["cost"], u in comp["carries"], u in buys_now(r["shop"]))
                          for u in board_order(comp, set_data)],
                "traits": [(t, bp, r["traits"].get(t, 0)) for t, n, bp in key_traits(comp, set_data) if n >= 2],
                # [(unit, items it holds, in your shop)] — the opener to play until the build comes together
                "early": [(e["unit"], len(e["items"]), e["unit"] in r["shop"]) for e in opener],
                # exact teamfight.lol board (positions, 3★, items per unit) when the comp has one
                "hex": comp.get("board"),
                "item_files": {n: icon_for(n) for b in comp.get("board", []) for n in b["items"]},
                "shop": buys_now(r["shop"]),
                # the plan / econ follow your build once you pin one; until then the standard plan, so they
                # don't change every time the top candidate does
                "plan": plan_now(comp) if locked else GENERAL_PLAN,
                "plan_bm": comp.get("plan_bm") if locked else None,
                "guide": guide_now(comp, r["round"]) if locked else None,  # how to play it, this stage
                "item_fill": item_fill,
                "item_next": None,  # the board only shows what your champions hold
            }
            shown_comp[0] = comp
            progress = reroll_progress(comp, bought) if locked else []
            advice = None if r["phase"] or preview else advise(
                econ, r["round"], comp.get("plan") if locked else GENERAL_PLAN, progress)
            if advice and pivot and advice[0].startswith("Strengthen"):  # say *what* to strengthen into
                advice = (advice[0], f"Switch to {pivot[0].replace(' — Standard', '')} now: play its units you "
                                     f"hold and roll for 2★.")
            if econ.get("gold") is not None:
                print(f"econ={econ} advice={advice}", flush=True)
            self.out.put({"status": "No game running — showing your pinned build" if preview else
                          f"Round {r['round']} · read {time.strftime('%H:%M:%S')}",
                          "traits": r["traits"], "items": item_text, "results": results,
                          "panel": panel, "board": board, "locked": locked, "advice": advice,
                          "pivot": pivot, "contest": contest, "lobby": lobby, "progress": progress,
                          # the build on the board needs something you don't have (you pinned it anyway)
                          "needs": results[0].get("needs_text") if results and results[0].get("needs") else None,
                          # pinned reroll build: which units to hold (the rest of the bench is to sell)
                          "keep": bench_priority(comp, bought) if locked and not preview else "",
                          # the popup over the game's shop: the 5 cards and which units to flash on them
                          "slots": r.get("slots") or [], "buy_units": popup_units(),
                          "level": econ.get("level"), "round": r["round"],
                          "streak": econ.get("streak")})

        # Poll only the round number (tiny grab, OCR skipped while it looks the same). Do a full
        # read when a new round starts, again once the board has settled, and on ⟳ — never
        # continuously, so the game keeps its CPU.
        while True:
            try:
                if self.paused.is_set():
                    time.sleep(0.3)
                    continue
                now = time.time()
                small = cap.grab(ROUND_REGION)
                if small is None:
                    # no game: the build you pick from the menu still shows (board, items, its whole guide)
                    if self.locked and (self.relock.is_set() or previewed != self.locked):
                        self.relock.clear()
                        previewed = self.locked
                        publish(dict(NO_GAME), preview=True)
                    elif previewed and not self.locked:  # unpinned: back to the empty screen
                        previewed = None
                        self.out.put({"status": "Waiting for TFT window…", "preview_clear": True})
                    self.out.put({"status": "No game running — showing your pinned build" if previewed
                                  else "Waiting for TFT window…"})
                    gone_since = gone_since or now
                    if now - gone_since > 120 and self.history.game:
                        self.history.finish()  # the game window's been gone 2 minutes: that game is over
                    time.sleep(self.cfg["interval_s"])
                    continue
                gone_since = previewed = None
                round_ = reader.read_round(small, cropped=True)
                new_game = round_ and round_.startswith("1-") and (last_round is None or last_round >= "2-1")
                if self.locked and (new_game or self.locked not in {c["name"] for c in comps}):
                    # stage 1 (after a later round, or the first round this run sees): a pin from an earlier
                    # game; or the pinned build no longer exists (guide sync). Either way: nothing pinned
                    self.locked = None
                    self.out.put({"status": "New game — build lock cleared", "unlock": True})
                if round_ and last_round and round_.startswith("1-") and last_round >= "2-1":
                    self._offered_25 = False
                    self._top = None
                    self._offer = None
                    self._hp_start.clear()
                    self._hp_ok = self._hp_odd = None
                    self.augments, self._aug_offers, self.aug_history = [], (None, []), []
                    self.manual.clear()
                    self.manual_items.clear()
                    self._items_ok.clear()
                    self._items_last.clear()
                    if scout:
                        scout.reset()
                if round_ and round_ != last_round:
                    last_round = round_
                    carousel_reads.clear()
                    delays = (1, 6)
                    schedule = [now + d for d in delays]
                if self.refresh.is_set():
                    self.refresh.clear()
                    schedule.insert(0, now)
                if self.report.is_set():
                    self.report.clear()
                    frame = cap.grab()
                    if frame is not None:
                        folder = ROOT / "tests" / "fixtures" / "reports"
                        folder.mkdir(parents=True, exist_ok=True)
                        stamp = time.strftime("%Y%m%d-%H%M%S")
                        Image.fromarray(frame).save(folder / f"{stamp}.png")
                        snap = dict(reading or {}, frame_read=full_read(frame, round_ or last_round),
                                    locked=self.locked, offer=self._offer, augments=self.augments,
                                    augment_offers=self._aug_offers)
                        (folder / f"{stamp}.json").write_text(json.dumps(snap, indent=1, default=str), encoding="utf8")
                        self.out.put({"status": f"Report saved: {stamp} — tell Claude what was wrong"})
                if not round_ and not schedule and now - last_full > 8:
                    schedule.append(now)  # round label unreadable: still read the screen every 8s
                # react to what you do, not just to the round timer: re-read (at most every 1.5s)
                # when the traits, item bench, augment cards, gold or level change
                if reading is not None and not schedule and now - last_full > 1.5 and \
                        reading.get("phase") != "carousel" and not self.cfg.get("log_only"):
                    wt = watch_thumb()
                    if wt is not None and watch_last[0] is not None and watch_changed(wt, watch_last[0]):
                        schedule.append(now)
                if schedule and now >= schedule[0] and not self.cfg.get("log_only"):
                    schedule.pop(0)
                    frame = cap.grab()
                    last_full = now
                    if frame is not None:
                        reading = full_read(frame, round_ or last_round)
                        publish(reading)
                        watch_last[0] = watch_thumb()
                elif self.relock.is_set() and reading:  # build changed: re-advise from the last read
                    self.relock.clear()
                    publish(reading)
                elif (reading and reading["phase"] == "carousel" and now >= next_carousel
                      and not self.cfg.get("log_only")):
                    # live carousel: players are released a pair at a time and take items, so
                    # re-read every few seconds and only offer what's still there
                    next_carousel = now + 1.5
                    frame = cap.grab()
                    if frame is not None:
                        carousel_reads.append(carousel_items(frame))
                        reading["offers"] = carousel_offers()
                        publish(reading)
                if scout and not (reading and reading.get("phase") == "carousel"):
                    tp = cap.grab(TRAIT_REGION)
                    if tp is not None:
                        scout.tick(tp)
                    if scout.due():
                        frame = cap.grab()
                        if frame is not None and scout.read(frame, round_ or last_round) and reading:
                            publish(reading)  # refresh the contest warning
                band = cap.grab(SHOP_BAND)
                if band is not None:
                    names = shoplog.quick(band)  # only right after the shop changed and settled
                    if names is not None and reading and not reading.get("phase"):
                        reading["shop"] = [u for u in names if u]
                        reading["slots"] = list(names)
                        self.out.put({"shop": buys_now(reading["shop"]),  # board flash only, no re-advise
                                      "slots": reading["slots"], "buy_units": popup_units()})
                    if not self.source:
                        shoplog.tick(band, round_ or last_round)
                if not self.source:
                    shoplog.flush()  # purchases + log: reads once the shop has been quiet a few seconds
                time.sleep(tick)

            except Exception:  # a bad read (odd OCR text, a frame from another window) must never
                # kill the overlay: log it, say so, keep going
                import traceback
                with (ROOT / "data" / "overlay_errors.log").open("a", encoding="utf8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
                self.out.put({"status": "Read error (logged to data/overlay_errors.log) — still running"})
                time.sleep(1)


class ItemPanel:
    """Canvas with item icons. Redrawn only when its data changes; between redraws a
    50ms tick animates glows, a gentle bob on the top pick, and a slide-in on new picks.
    `s` scales everything (1 = compact overlay, bigger when maximised)."""

    def __init__(self, parent, width, s=1.0, on_item=None):
        self.w, self.s, self.on_item = width, s, on_item
        self.c = tk.Canvas(parent, width=width, height=self.P(PANEL_H), bg=BG, highlightthickness=0)
        self.c.pack(pady=(2, 4), anchor="w")
        self.images = {}
        self.champ_icons = IconCache()
        self.glows, self.bobs, self.slide = [], [], []
        self._sig, self._phase = None, None
        self.t = 0.0
        self.c.create_text(width // 2, self.P(PANEL_H) // 2, text="Waiting for game…", fill=DIM,
                           font=self.F(9))

    def P(self, v):
        return int(round(v * self.s))

    def F(self, size, *style):
        return ("Segoe UI", max(6, int(round(size * self.s))), *style)

    def icon(self, file, size, dim=False):
        key = (file, size, dim)
        if key not in self.images:
            path = DATA / "icons" / file if file else None
            if not path or not path.exists():
                return None
            img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
            if dim:
                img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(0.2)).enhance(0.45)
            self.images[key] = ImageTk.PhotoImage(img)
        return self.images[key]

    def _draw_icon(self, x, y, size, file, label, dim=False, glow=False, tags=()):
        P = self.P
        img = self.icon(file, size, dim)
        ids = []
        if img:
            ids.append(self.c.create_image(x, y, image=img, anchor="nw", tags=tags))
        else:  # no art (unknown special item): labelled tile
            ids.append(self.c.create_rectangle(x, y, x + size, y + size, fill=CARD, outline=DIM, tags=tags))
            ids.append(self.c.create_text(x + size / 2, y + size / 2, text=label[:6], fill=FG,
                                          font=self.F(7), tags=tags))
        ring = self.c.create_rectangle(x - P(3), y - P(3), x + size + P(3), y + size + P(3),
                                       outline=ACCENT if glow else "#000000", width=P(2) if glow else 1,
                                       tags=tags)
        ids.append(ring)
        if glow:
            self.glows.append(ring)
        return ids

    def update(self, panel, force=False):
        sig = json.dumps(panel, sort_keys=True, default=str)
        if sig == self._sig and not force:
            return
        self._sig = sig
        entering_pick = panel.get("phase") and panel["phase"] != self._phase
        self._phase = panel.get("phase")
        self.c.delete("all")
        self.glows, self.bobs, self.slide = [], [], []
        if panel.get("phase") == "augment":  # augments aren't items: their own cards
            self._draw_picks(panel, entering_pick)
        else:  # carousel / armory included: the builds stay, offered pieces flash in them
            self._draw_build(panel)

    def _draw_build(self, panel):
        """The guide's builds, exactly as the guide lists them: each carry, its items, the two
        components under each item. Nothing here changes during a game — what your champions hold is
        on the board. On carousel / armory the one piece worth grabbing flashes where it goes."""
        c, P, bold = self.c, self.P, self.F(8, "bold")
        flash = panel.get("flash")
        size, step, cs = P(34), P(38), P(16)
        groups = 4 if self.w >= P(620) else 2  # wide: every carry's full build, like teamfight.lol
        for g, (unit, lst) in enumerate(panel["targets"][:groups]):
            x0 = g * (self.w // groups)
            t = c.create_text(x0, P(2), text=unit, anchor="nw", fill=FG, font=bold)
            if unit in (panel.get("stars") or []):  # a 3★ target of this reroll
                c.create_text(c.bbox(t)[2] + P(6), P(2), text="★★★", anchor="nw", fill=ACCENT, font=bold)
            for k, (file, name, comps, *must) in enumerate(lst):
                x, y = x0 + P(2) + k * step, P(18)
                here = bool(flash) and flash[0] == unit and flash[1] == k
                self._draw_icon(x, y, size, file, name)
                if must and must[0]:  # the guide's must-have item: a gold asterisk on its corner
                    c.create_oval(x + size - P(11), y - P(5), x + size + P(5), y + P(11), fill="#111", outline=ACCENT)
                    c.create_text(x + size - P(3), y + P(4), text="*", fill=ACCENT, font=self.F(13, "bold"))
                if here and flash[2] is None:  # the finished item itself is on offer
                    c.create_rectangle(x - P(3), y - P(3), x + size + P(3), y + size + P(3), width=P(3),
                                       outline=ACCENT, tags=("flashbest",))
                for m, cfile in enumerate(comps):  # its two components, under it
                    cx_, cy_ = x + m * (cs + P(2)), y + size + P(4)
                    img = self.icon(cfile, cs, False)
                    if img:
                        c.create_image(cx_, cy_, image=img, anchor="nw")
                    if here and flash[2] == m:
                        c.create_rectangle(cx_ - P(2), cy_ - P(2), cx_ + cs + P(2), cy_ + cs + P(2), width=P(3),
                                           outline=ACCENT, tags=("flashbest",))

    def _draw_picks(self, panel, animate_in):
        c, P = self.c, self.P
        title = {"carousel": "CAROUSEL — grab the first one still there",
                 "armory": "ARMORY — best pick for your build",
                 "augment": "AUGMENT — teamfight.lol's picks for your comp"}[panel["phase"]]
        c.create_text(0, P(2), text=title, anchor="nw", fill=ACCENT, font=self.F(8, "bold"))
        picks = panel.get("picks") or []
        if not picks:
            c.create_text(self.w // 2, P(60), text="No items recognised yet…", fill=DIM, font=self.F(8))
            return
        col = self.w // 3
        for i, (name, score, reason, file, unit, unit_icon) in enumerate(picks):
            tag = f"pick{i}"
            cx, size = i * col + col // 2, P(40) if i == 0 else P(34)
            x, y = cx - size // 2 - P(6), P(26) + (0 if i == 0 else P(4))
            useful = score > 10
            self._draw_icon(x, y, size, file, name, dim=not useful, glow=i == 0 and useful, tags=(tag,))
            c.create_oval(x - P(8), y - P(8), x + P(6), y + P(6), fill=ACCENT if i == 0 else CARD,
                          outline="", tags=(tag,))
            c.create_text(x - P(1), y - P(1), text=str(i + 1), fill="#111" if i == 0 else FG,
                          font=self.F(7, "bold"), tags=(tag,))
            if unit:  # who it's for: their portrait as a badge on the item, so it can't be misread
                bsz = int(size * 0.62)
                bx, by = x + size - bsz // 3, y + size - bsz + P(4)
                img = self.champ_icons.get(unit_icon, bsz, folder="champ_icons")
                if img:
                    c.create_image(bx, by, image=img, anchor="nw", tags=(tag,))
                c.create_rectangle(bx - 1, by - 1, bx + bsz + 1, by + bsz + 1, outline=ACCENT, width=P(2),
                                   tags=(tag,))
            yy = c.bbox(c.create_text(cx, P(70), text=name, fill=FG if useful else DIM, width=col - P(6),
                                      anchor="n", font=self.F(7, "bold"), justify="center", tags=(tag,)))[3]
            target = f"for {unit}" if unit else ("—" if not useful else "any unit")
            yy = c.bbox(c.create_text(cx, yy + P(1), text=target, fill=ACCENT if unit else DIM, anchor="n",
                                      width=col - P(6), font=self.F(8, "bold"), justify="center",
                                      tags=(tag,)))[3]
            c.create_text(cx, yy + P(1), text=reason, fill=GREEN if useful else DIM, width=col - P(6),
                          anchor="n", font=self.F(6), justify="center", tags=(tag,))
            if i == 0 and useful:
                self.bobs.append(tag)
            if animate_in:  # start off to the right, staggered
                c.move(tag, P(60 + 25 * i), 0)
                self.slide.append([tag, P(60 + 25 * i)])

    def tick(self):
        """Animation frame: pulse glows, bob the top pick, slide new picks in, flash what's offered
        on the carousel / armory (the best one in gold)."""
        on = int(time.time() * 3) % 2 == 0
        self.c.itemconfig("flashcomp", outline=GREEN if on else "#ffffff")
        self.c.itemconfig("flashbest", outline=ACCENT if on else "#ffffff")
        if not (self.glows or self.bobs or self.slide):
            return
        self.t += 0.05
        pulse = (math.sin(self.t * 5) + 1) / 2
        color = mix("#5a4a20", ACCENT, pulse)
        for ring in self.glows:
            self.c.itemconfigure(ring, outline=color, width=self.P(2 + round(pulse)))
        dy = math.cos(self.t * 3) * 0.5 * self.s  # derivative of a 1.5px sine bob
        for tag in self.bobs:
            self.c.move(tag, 0, dy)
        for sl in self.slide:
            if sl[1] > 0:
                step = max(1, sl[1] * 0.25)
                self.c.move(sl[0], -step, 0)
                sl[1] -= step


class BoardPanel:
    """The main view, like teamfight.lol's team builder: the comp's exact board, big — champion
    portraits on their hexes, 3★ stars, items on each carrier. Champions in your shop flash.
    Traits down the left (have / breakpoint) when there's room, else under the board."""

    def __init__(self, parent, width, height, s):
        self.w, self.h, self.s = width, height, s
        self.icons = IconCache()
        self.champs = load_set()["champions"]
        self.c = tk.Canvas(parent, width=width, height=height, bg=BG, highlightthickness=0)
        self.c.pack(anchor="nw")
        self.board, self._sig = None, None
        self.c.create_text(width // 2, height // 3, text="Waiting for game…", fill=DIM, font=self.F(10))

    def P(self, v):
        return int(round(v * self.s))

    def F(self, size, *style):
        return ("Segoe UI", max(6, int(round(size * self.s))), *style)

    def update(self, board):
        self.board = board
        self._draw()

    def set_shop(self, shop):
        """Quick shop read while you roll: only the flashing changes."""
        if self.board is not None:
            self.board = dict(self.board, shop=list(shop))
            self._draw()

    def tick(self):
        """50ms: flash the champions in your shop (green / white)."""
        on = int(time.time() * 3) % 2 == 0
        self.c.itemconfig("flashring", outline=GREEN if on else "#ffffff")
        self.c.itemconfig("flashbadge", fill=GREEN if on else "#ffffff")
        self.c.itemconfig("flashtext", fill=GREEN if on else "#ffffff")

    def _draw(self):
        board = self.board
        sig = json.dumps(board, sort_keys=True)
        if sig == self._sig:
            return
        self._sig = sig
        c, P = self.c, self.P
        c.delete("all")
        # header: comp name, then what to buy right now (big, green)
        tier = board.get("tier") or ""
        x = 0
        if tier:
            t = c.create_text(P(6), P(2), text=tier, anchor="nw", fill="#111", font=self.F(11, "bold"))
            x1, y1, x2, y2 = c.bbox(t)
            c.tag_lower(c.create_rectangle(x1 - P(5), y1, x2 + P(5), y2, fill=TIER_COLOR.get(tier, DIM),
                                           outline=""), t)
            x = x2 + P(12)
        t = c.create_text(x, P(2), text=board.get("title") or board["comp"].replace(" — Standard", ""), anchor="nw",
                          fill=FG, font=self.F(13, "bold"))
        y = c.bbox(t)[3] + P(4)
        if board.get("badge"):  # what kind of build this is: what you roll for, at which level
            bt = c.create_text(c.bbox(t)[2] + P(14), P(6), text=board["badge"], anchor="nw", fill="#111",
                               font=self.F(10, "bold"))
            x1, y1, x2, y2 = c.bbox(bt)
            c.tag_lower(c.create_rectangle(x1 - P(6), y1 - P(2), x2 + P(6), y2 + P(2), fill=ACCENT, outline=""), bt)
            t = bt
        buys = [u for u in dict.fromkeys(board.get("shop", ())) if any(u == b[0] for b in board["units"])]
        if buys:  # what to buy, on the same line as the comp name when there's room
            bx_ = c.bbox(t)[2] + P(24)
            same = self.w >= P(700)
            t2 = c.create_text(bx_ if same else 0, P(2) if same else y, text="BUY  " + "  ·  ".join(buys),
                               anchor="nw", fill=GREEN, font=self.F(12, "bold"), tags=("flashtext",))
            if not same:
                y = c.bbox(t2)[3] + P(4)
        if board.get("early"):  # stages 2-3: the opener to play while the build comes together (green: in shop)
            t = c.create_text(0, y, text="EARLY", anchor="nw", fill=DIM, font=self.F(9, "bold"))
            x = c.bbox(t)[2] + P(8)
            for k, (unit, n_items, in_shop) in enumerate(board["early"]):
                label = unit + (f" ({n_items} items)" if n_items else "") + ("  ·" if k < len(board["early"]) - 1 else "")
                t = c.create_text(x, y, text=label, anchor="nw", fill=GREEN if in_shop else FG, font=self.F(10, "bold"))
                x = c.bbox(t)[2] + P(6)
            y = c.bbox(t)[3] + P(4)
        top = y + P(4)
        side = self.w >= P(560)  # traits as a column on the left, like teamfight.lol
        tw = P(92) if side else 0  # trait icons + counts
        bx = tw + (P(12) if side else 0)
        avail_w = self.w - bx
        if board.get("hex"):  # exact teamfight.lol board: positions, 3★, items per unit
            reserve = 0 if side else P(48)  # traits line under the board in compact mode
            bw = int(min(avail_w, (self.h - top - reserve - P(6)) / 0.585))
            files = board.get("item_files", {})
            bottom = draw_hex_board(c, bx + (avail_w - bw) // 2, top, bw, board["hex"], self.icons, self.champs,
                                    lambda n: files.get(n), shop=board.get("shop", ()), font=("Segoe UI",),
                                    later=board.get("later", ()), item_fill=board.get("item_fill", {}),
                                    item_next=board.get("item_next"))
        else:
            bottom = self._unit_row(board, bx, top, avail_w)
        if side:
            self._traits_column(board, 0, top, tw)
        else:
            self._traits_line(board, bottom + P(4))

    def _unit_row(self, board, x0, y, width):
        """No exact board for this comp: its units in fielding order, big portraits."""
        c, P = self.c, self.P
        units = board["units"][:9]
        step = width / max(len(units), 9)
        size = int(min(step * 0.9, P(80)))
        for i, (unit, icon, cost, carry, in_shop) in enumerate(units):
            x = int(x0 + i * step)
            img = self.icons.get(icon, size, folder="champ_icons")
            if img:
                c.create_image(x, y, image=img, anchor="nw")
            ring = GREEN if in_shop else (ACCENT if carry else COST_COLOR.get(cost, DIM))
            c.create_rectangle(x - 1, y - 1, x + size + 1, y + size + 1, outline=ring,
                               width=P(4) if (carry or in_shop) else P(2), tags=("flashring",) if in_shop else ())
            t = c.create_text(x + size / 2, y + size + P(9), text=f"BUY {unit}" if in_shop else unit,
                              fill="#111" if in_shop else (FG if carry else DIM), font=self.F(8, "bold"))
            if in_shop:  # flashing name strip; the portrait stays visible
                x1, y1, x2, y2 = c.bbox(t)
                c.tag_lower(c.create_rectangle(x1 - P(3), y1, x2 + P(3), y2, fill=GREEN, outline="",
                                               tags=("flashbadge",)), t)
        return y + size + P(20)

    def _traits_column(self, board, x, y, width):
        """Comp traits down the left as icons with 'have/needed': lit green when active, grey when
        you have some, dark when none."""
        c, P = self.c, self.P
        tile = P(38)
        for trait, bp, have in board["traits"]:
            done = have >= bp
            fill = "#2f5a3a" if done else ("#2a2d36" if have else "#16181e")
            c.create_rectangle(x, y, x + tile, y + tile, fill=fill, outline=GREEN if done else "#3a3f4b",
                               width=P(2) if done else 1)
            img = self.icons.get(f"{trait}.png", int(tile * 0.74), dim=not have, folder="trait_icons")
            if img:
                c.create_image(x + tile / 2, y + tile / 2, image=img)
            else:
                c.create_text(x + tile / 2, y + tile / 2, text=trait[:3], fill=FG, font=self.F(8, "bold"))
            c.create_text(x + tile + P(8), y + tile / 2, text=f"{have}/{bp}", anchor="w",
                          fill=GREEN if done else (FG if have else DIM), font=self.F(11, "bold"))
            y += tile + P(6)

    def _traits_line(self, board, y):
        c, P = self.c, self.P
        x = 0
        for trait, bp, have in board["traits"]:
            done = have >= bp
            tid = c.create_text(x, y, text=f"{trait} {have}/{bp}", anchor="nw",
                                fill=GREEN if done else (FG if have else DIM), font=self.F(8, "bold" if done else ""))
            x = c.bbox(tid)[2] + P(10)
            if x > self.w - P(70):
                x, y = 0, y + P(15)
        return y + P(15)


class SidePanel:
    """Everything that isn't the board or the items, as pictures instead of paragraphs: one big
    gold line, pivot / contest banners, other comps as portrait cards (click to pin), and the
    plan as chips."""

    def __init__(self, parent, width, height, s, on_pin, on_have=None, on_keep=None, levels=True):
        self.w, self.h, self.s, self.on_pin, self.on_have, self.on_keep = width, height, s, on_pin, on_have, on_keep
        self.levels = levels  # draw the leveling plan here (max mode: LevelPanel under the items does)
        self.icons = IconCache()
        self.champs = load_set()["champions"]
        self.c = tk.Canvas(parent, width=width, height=height, bg=BG, highlightthickness=0)
        self.c.pack(anchor="nw", pady=(self.P(6), 0))
        self._sig = None
        self._bottom = height  # top of the gold line at the bottom
        self._last = None
        # the window may give the panel less than it asked for (compact mode): redraw at the real size
        self.c.bind("<Configure>", lambda _e: self._last and self.update(self._last))

    def _height(self):
        """What the window actually shows of the panel (compact mode clips it), else the size asked for."""
        h = self.c.winfo_height()
        return min(h, self.h) if h > 50 else self.h

    def P(self, v):
        return int(round(v * self.s))

    def F(self, size, *style):
        return ("Segoe UI", max(6, int(round(size * self.s))), *style)

    @staticmethod
    def short(name):
        return name.replace(" — Standard", "")

    def update(self, msg):
        sig = json.dumps([msg.get("advice"), msg.get("pivot"), msg.get("contest"), msg.get("progress"), msg.get("needs"),
                          msg.get("keep"),
                          [(r["comp"]["name"], r.get("missing_core")) for r in msg["results"][:3]],
                          msg.get("locked"), (msg.get("board") or {}).get("plan"), msg.get("level"),
                          msg.get("round"), (msg.get("board") or {}).get("plan_bm"), msg.get("streak"),
                          (msg.get("board") or {}).get("guide"), self._height()], default=str)
        self._last = msg
        if sig == self._sig:
            return
        self._sig = sig
        self.c.delete("all")
        # gold / level advice at the bottom, pinned or not; everything else fills the space above it
        board = msg.get("board") or {}
        plan_args = (board.get("plan"), msg.get("level"), msg.get("round"), board.get("plan_bm"), msg.get("streak"))
        self._bottom = self._height()
        self._bottom -= self._econ(msg.get("advice"))
        self._bottom -= self._keep(msg.get("keep"))
        strip_top = self._bottom
        if self.levels:
            self._bottom -= self._strip(*plan_args)  # leveling, compact: always room for it
        y = self._banners(msg, 0)
        if not msg.get("locked"):  # pick once: the top builds for your opener, click one to pin it
            y = self._others(msg["results"][:3], y, "PICK A BUILD")
        else:  # your build: what its guide says to do this stage (the leveling chips go under it if they fit)
            y = self._guide(board.get("guide"), y)
        self._bottom = strip_top  # the big chips may use the strip's room too...
        if self.levels and self._plan(plan_args[0], y, *plan_args[1:])[1]:
            self.c.delete("strip")  # ...and when they fit, the strip isn't needed

    def _title(self, text, y, tags=()):
        self.c.create_text(0, y, text=text, anchor="nw", fill=DIM, font=self.F(8, "bold"), tags=tags)
        return y + self.P(18)

    def _guide(self, g, y):
        """The pinned build's guide for this stage: 'STAGE 3' and the author's line for it, then item holders /
        emblem, then the gameplan (stages 1-2) and tips while there's room above the bottom lines. -> y."""
        if not g:
            return y
        c, P = self.c, self.P
        room = self._bottom - P(6)
        blocks = []  # (title, text, color, font size)
        if g.get("stage") and self.levels:  # max mode: the stage line sits under the leveling chips instead
            blocks.append((f"GUIDE · {g['stage'][0].upper()}", g["stage"][1], FG, 10))
        hold = "  ·  ".join(filter(None, [g.get("holders") and f"Item holders: {g['holders']}",
                                          g.get("emblem") and f"Emblem on: {g['emblem']}"]))
        if hold:
            blocks.append(("", hold, ACCENT, 9))
        if g.get("gameplan"):
            blocks.append(("GAMEPLAN", g["gameplan"], FG, 9))
        for i, tip in enumerate(g.get("tips") or []):
            blocks.append(("TIPS" if i == 0 else "", "• " + tip, DIM, 9))
        for i, (unit, text, *_) in enumerate(g.get("items") or []):
            blocks.append(("ITEMS" if i == 0 else "", f"{unit}: {text}", DIM, 9))
        for k, (title, text, color, size) in enumerate(blocks):
            before = y
            if title:
                y = self._title(title, y, tags=("guide_new",))
            while True:
                t = c.create_text(0, y, text=text, anchor="nw", fill=color,
                                  font=self.F(size, "bold" if size >= 10 else ""), width=self.w - P(4), tags=("guide_new",))
                bottom = c.bbox(t)[3] + P(4)
                if bottom <= room or k > 0 or len(text) < 60:
                    break
                c.delete(t)  # the stage line always shows: shortened when the panel is short (compact)
                text = text[:int(len(text) * 0.75)].rsplit(" ", 1)[0] + "…"
            if bottom > room:  # doesn't fit: stop here
                c.delete("guide_new")
                return before
            c.dtag("guide_new", "guide_new")
            y = bottom
        return y + P(6)

    def _keep(self, text):
        """The bench line above the gold line: 'HOLD  Veigar 6/9 · LeBlanc 4/9 · Ornn ×3 …  sell the rest'.
        -> the height it takes."""
        if not text:
            return 0
        c, P = self.c, self.P
        t = c.create_text(0, P(4), text=f"HOLD  {text}  ·  sell the rest", anchor="nw", fill=GREEN,
                          font=self.F(9, "bold"), width=self.w, tags=("keep",))
        y = c.bbox(t)[3] + P(2)
        c.move("keep", 0, self._bottom - y)
        return y

    def _econ(self, adv):
        """The gold / level line, drawn at the bottom of the panel: '$ Level now: 20g → Lv 7' and the short
        reason under it. -> the height it takes."""
        if not adv:
            return 0
        c, P = self.c, self.P
        t = c.create_text(0, P(6), text="$ " + adv[0], anchor="nw", fill=ACCENT, font=self.F(12, "bold"),
                          width=self.w, tags=("econ",))
        y = c.bbox(t)[3]
        # the reason only when it's short; 3★ progress and the plan are drawn above anyway
        why = (adv[1] or "").split(" — ")[0].split(". ")[0].split(" (")[0]
        if why and len(why) <= 56:
            t = c.create_text(P(14), y, text=why, anchor="nw", fill=FG, font=self.F(8), tags=("econ",))
            y = c.bbox(t)[3]
        y += P(4)
        c.create_line(0, P(2), self.w, P(2), fill="#3a3f4b", tags=("econ",))
        c.move("econ", 0, self._height() - y)
        return y

    def _banner(self, y, color, head, sub, comp=None, tag=None):
        """Coloured strip: big head, short sub, the comp's carry portraits on the right."""
        c, P = self.c, self.P
        hgt = P(54)
        c.create_rectangle(0, y, self.w, y + hgt, fill=CARD, outline=color, width=P(2), tags=(tag,) if tag else ())
        c.create_rectangle(0, y, P(6), y + hgt, fill=color, outline="", tags=(tag,) if tag else ())
        c.create_text(P(16), y + P(8), text=head, anchor="nw", fill=color, font=self.F(11, "bold"),
                      tags=(tag,) if tag else ())
        c.create_text(P(16), y + P(31), text=sub, anchor="nw", fill=FG, font=self.F(8), tags=(tag,) if tag else ())
        if comp:
            size, x = P(40), self.w - P(8)
            for unit in list(comp.get("carries") or {})[:3][::-1]:
                img = self.icons.get(self.champs.get(unit, {}).get("icon"), size, folder="champ_icons")
                if img:
                    x -= size + P(4)
                    c.create_image(x, y + P(7), image=img, anchor="nw", tags=(tag,) if tag else ())
        if tag:
            c.tag_bind(tag, "<Button-1>", lambda _e, n=tag: self.on_pin(self._pin_target[n]))
            c.tag_bind(tag, "<Enter>", lambda _e: c.config(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda _e: c.config(cursor=""))
        return y + hgt + P(8)

    def _banners(self, msg, y):
        self._pin_target = {}
        comps = {r["comp"]["name"]: r["comp"] for r in msg["results"]}
        pv = msg.get("pivot")
        if pv:
            self._pin_target["pivot"] = pv[0]
            auto = len(pv) > 2 and pv[2]
            reason = pv[1].split(" — ")[0].split(" (")[0]
            y = self._banner(y, GREEN, ("SWITCHED → " if auto else "PIVOT → ") +
                             ((comps.get(pv[0]) or {}).get("guide_name") or self.short(pv[0])),
                             reason + ("  ·  click to pin" if auto else "  ·  click to switch"),
                             comps.get(pv[0]), tag="pivot")
            # wrong? one click: "I have Kha'Zix" (orb / carousel units never show as purchases)
            x = 0
            for unit in (pv[3] if len(pv) > 3 and pv[3] else []):
                tag = f"have_{unit}"
                t = self.c.create_text(x + self.P(10), y + self.P(4), text=f"✓ I have {unit}", anchor="nw",
                                       fill="#111", font=self.F(9, "bold"), tags=(tag,))
                x1, y1, x2, y2 = self.c.bbox(t)
                self.c.tag_lower(self.c.create_rectangle(x1 - self.P(10), y1 - self.P(4), x2 + self.P(10),
                                                         y2 + self.P(4), fill=GREEN, outline="", tags=(tag,)), t)
                self.c.tag_bind(tag, "<Button-1>", lambda _e, u=unit: self.on_have and self.on_have(u))
                self.c.tag_bind(tag, "<Enter>", lambda _e: self.c.config(cursor="hand2"))
                self.c.tag_bind(tag, "<Leave>", lambda _e: self.c.config(cursor=""))
                x = x2 + self.P(20)
            # or keep your build: the offer goes away
            tag = "keep"
            t = self.c.create_text(x + self.P(10), y + self.P(4), text="✕ keep my build", anchor="nw",
                                   fill=FG, font=self.F(9, "bold"), tags=(tag,))
            x1, y1, x2, y2 = self.c.bbox(t)
            self.c.tag_lower(self.c.create_rectangle(x1 - self.P(10), y1 - self.P(4), x2 + self.P(10),
                                                     y2 + self.P(4), fill=CARD, outline=DIM, tags=(tag,)), t)
            self.c.tag_bind(tag, "<Button-1>", lambda _e: self.on_keep and self.on_keep())
            self.c.tag_bind(tag, "<Enter>", lambda _e: self.c.config(cursor="hand2"))
            self.c.tag_bind(tag, "<Leave>", lambda _e: self.c.config(cursor=""))
            y += self.P(36)
        ct = msg.get("contest") or []
        if ct:
            who = ", ".join(n for n, _, _ in ct[:2])
            what = ct[0][2].split(",")[0]
            y = self._banner(y, "#ff8a7a", f"⚠ CONTESTED ({len(ct)})", f"{who}: {what}")
        need = msg.get("needs")
        if need:  # what this build needs that you don't have: plain to see before you commit to it
            y = self._banner(y, "#ffb347", "NEEDS", need if len(need) <= 64 else need[:62] + "…")
        return y + (self.P(4) if pv or ct or need else 0)

    def _others(self, results, y, title="OTHER OPTIONS"):
        """Builds to choose from: tier, name, carry portraits. Click = pin it."""
        if not results:
            return y
        c, P = self.c, self.P
        y = self._title(title, y)
        # all of them above the gold line: cards get shorter (down to 30) when the panel is short (compact)
        gap = P(6)
        hgt = max(P(30), min(P(44), (self._bottom - y) // len(results) - gap))
        size = hgt - P(10)
        for i, r in enumerate(results):
            if y + hgt > self._bottom:  # no room above the gold line
                break
            comp, tag = r["comp"], f"alt{i}"
            self._pin_target[tag] = comp["name"]
            c.create_rectangle(0, y, self.w, y + hgt, fill=CARD, outline="", tags=(tag,))
            tier = r.get("tier_now") or comp.get("tier") or "?"
            t = c.create_text(P(10), y + hgt / 2, text=tier, anchor="w", fill="#111", font=self.F(10, "bold"),
                              tags=(tag,))
            x1, y1, x2, y2 = c.bbox(t)
            c.tag_lower(c.create_rectangle(x1 - P(4), y1, x2 + P(4), y2, fill=TIER_COLOR.get(tier, DIM),
                                           outline="", tags=(tag,)), t)
            name = comp.get("guide_name") or self.short(comp["name"])
            miss = r.get("missing_core")
            hit = r.get("hit")
            c.create_text(x2 + P(12), y + hgt / 2, text=name + (f"  · you have {hit}" if hit else
                                                                 f"  ⚠ no {miss[0]}" if miss else ""), anchor="w",
                          fill=GREEN if hit else "#ff8a7a" if miss else FG, font=self.F(10, "bold"), tags=(tag,))
            x = self.w - P(6)
            for unit in list(comp.get("carries") or {})[:3][::-1]:
                img = self.icons.get(self.champs.get(unit, {}).get("icon"), size, folder="champ_icons")
                if img:
                    x -= size + P(4)
                    c.create_image(x, y + (hgt - size) / 2, image=img, anchor="nw", tags=(tag,))
            c.tag_bind(tag, "<Button-1>", lambda _e, n=tag: self.on_pin(self._pin_target[n]))
            c.tag_bind(tag, "<Enter>", lambda _e: c.config(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda _e: c.config(cursor=""))
            y += hgt + P(6)
        return y + P(10)

    @staticmethod
    def _steps(plan, plan_bm, streak, round_):
        """The leveling plan to show: (title, [(level, round, note)], text, index of the step due by now).
        bunnymuffins' own pattern (the row for your streak) when the build has one, its text when it's only
        words (1-cost rerolls: 'don't press level until you 3 star'), else the overlay's plan."""
        rows = (plan_bm or {}).get("rows") or {}
        if rows:
            want = "Lose Streak" if (streak or 0) <= -2 else "Win Streak" if (streak or 0) >= 2 else "Mixed Streak"
            name = want if want in rows else ("Other" if "Other" in rows else next(iter(rows)))
            title, steps, text = f"LEVELING · {name.lower()} (bunnymuffins)", list(rows[name]), ""
        elif (plan_bm or {}).get("text"):
            title, steps, text = "LEVELING (bunnymuffins)", [], plan_bm["text"].split(". ")[0] + "."
        else:
            title, steps, text = "LEVELING PLAN", [], ""
            for stage, what in (plan or {}).get("leveling") or []:
                m = re.search(r"Lv\s*(\d+)", what)
                note = re.sub(r"Lv\s*\d+\s*·?\s*", "", what).replace("only once 3★ done", "after 3★").replace(
                    "slow roll above 50g", "slow roll").strip(" ·")
                steps.append((int(m.group(1)) if m else None, stage, note))
        due = None  # the step the plan expects by now (rounds as "3-5" compare as text)
        for k, (lv, stage, _) in enumerate(steps):
            if round_ and stage.split("/")[0].split(" or ")[0].strip().rstrip("+") <= round_:
                due = k
        return title, steps, text, due

    def _strip(self, plan, level=None, round_=None, plan_bm=None, streak=None):
        """The leveling plan as one compact line above the HOLD / gold lines, for when the chips don't fit:
        'LEVEL  2-1 Lv4 · 2-5 Lv5 · 3-2 Lv6 · 3-5 Lv7 · 4-1 Lv8 roll' — your level green, a step you're behind on
        red, roll steps gold. -> the height it takes."""
        if not (plan or plan_bm):
            return 0
        c, P = self.c, self.P
        _, steps, text, due = self._steps(plan, plan_bm, streak, round_)
        x, y, line = 0, P(4), P(16)
        t = c.create_text(x, y, text="LEVEL", anchor="nw", fill=DIM, font=self.F(8, "bold"), tags=("strip",))
        x = c.bbox(t)[2] + P(6)
        parts = [(text if len(text) <= 70 else text[:68] + "…", FG)] if text else []
        for k, (lv, stage, note) in enumerate(steps):
            mine = level is not None and lv == level
            behind = k == due and level is not None and lv is not None and level < lv
            color = "#ff8a7a" if behind else GREEN if mine else ACCENT if "roll" in note else FG
            parts.append((f"{stage} " + (f"Lv{lv}" if lv else "") + (" roll" if "roll" in note else "")
                          + ("  ·" if k < len(steps) - 1 else ""), color))
        for label, color in parts:
            t = c.create_text(x, y, text=label, anchor="nw", fill=color, font=self.F(9, "bold"),
                              width=self.w - P(4), tags=("strip",))
            x1, _, x2, y2 = c.bbox(t)
            if x2 > self.w and x > P(40):  # doesn't fit on this line: next line
                c.move(t, P(40) - x1, line)
                y += line
                x1, _, x2, y2 = c.bbox(t)
            x = x2 + P(2)
        bottom = c.bbox("strip")[3] + P(2)
        c.move("strip", 0, self._bottom - bottom)
        return bottom

    def _plan(self, plan, y, level=None, round_=None, plan_bm=None, streak=None, stages=None):
        """Leveling plan as chips, level first and big ('Lv 8'), the round and note underneath — only when
        all of them fit above the bottom lines (else the compact strip shows it). The step for your
        current level says YOU; if the plan wants a higher level by now, that step is red with your actual
        level — so 'Lv 7 slow roll' is never mistaken for the level 8 roll-down. -> (y, drawn)."""
        if not (plan or plan_bm):
            return y, False
        c, P = self.c, self.P
        title, steps, text, due = self._steps(plan, plan_bm, streak, round_)
        cw, ch = P(118), P(48)
        per_row = max(1, (self.w + P(8)) // (cw + P(8)))
        need = P(18) + (P(40) if text else -(-len(steps) // per_row) * (ch + P(8)))
        if y + need > self._bottom:
            return y, False
        y = self._title(title, y)
        if text:
            t = c.create_text(0, y, text=text, anchor="nw", fill=FG, font=self.F(10, "bold"), width=self.w)
            return c.bbox(t)[3] + P(10), True
        x, top = 0, y
        chips = []  # (rectangle, its outline, its width, the guide's [label, text] for that step's stage)
        for k, (lv, stage, note) in enumerate(steps):
            if x + cw > self.w and x > 0:
                x, top = 0, top + ch + P(8)
            mine = level is not None and lv == level
            behind = k == due and level is not None and lv is not None and level < lv
            roll = "roll" in note
            border = "#ff8a7a" if behind else GREEN if mine else (ACCENT if roll else "#3a3f4b")
            tag = f"chip{k}"
            rect = c.create_rectangle(x, top, x + cw, top + ch, fill=CARD, outline=border,
                                      width=P(3) if (behind or mine) else 1, tags=(tag,))
            c.create_text(x + P(8), top + P(4), text=f"Lv {lv}" if lv else stage, anchor="nw",
                          fill="#ff8a7a" if behind else (GREEN if mine else FG), font=self.F(14, "bold"), tags=(tag,))
            you = f"you're Lv {level}" if behind else ("YOU" if mine else "")
            if you:
                c.create_text(x + cw - P(6), top + P(6), text=you, anchor="ne", fill="#ff8a7a" if behind else GREEN,
                              font=self.F(8, "bold"), tags=(tag,))
            c.create_text(x + P(8), top + P(29), text=f"{stage}" + (f" · {note}" if note else ""), anchor="nw",
                          fill=ACCENT if roll else DIM, font=self.F(8, "bold" if roll else ""), width=cw - P(12),
                          tags=(tag,))
            m = re.search(r"(\d)-\d", stage)
            chips.append((rect, border, P(3) if (behind or mine) else 1,
                          stage_for(stages, int(m.group(1))) if stages and m else None))
            x += cw + P(8)
        y = top + ch + (P(4) if stages else P(10))
        if stages:  # under the chips: the guide's advice for the step you're on (no game yet: the first step;
            y = self._stage_advice(chips, due if due is not None else 0, y)  # hovering a chip shows its stage)
        return y, True

    def _stage_advice(self, chips, cur, y):
        """The guide's line for the highlighted step's stage under the chips ('STAGE 4 ▸ Roll for Ahri 2, Morgana
        2 …'); hovering a chip highlights it and shows its stage, leaving goes back to the step you're on."""
        c, P = self.c, self.P
        room = self._bottom - P(2)
        if y + P(16) > room:
            return y
        body = c.create_text(0, y, text="", anchor="nw", fill=FG, font=self.F(10), width=self.w - P(4))

        def show(k, hover=False):
            for i, (rect, outline, width, _) in enumerate(chips):
                lit = hover and i == k
                c.itemconfig(rect, outline=ACCENT if lit else outline, width=P(3) if lit else width)
            line = chips[k][3] if k is not None else None
            if k is None:
                text = "GUIDE ▸ hover a step to see what the guide says for that stage."
            elif not line:
                text = "GUIDE ▸ no stage advice for this step."
            else:
                text = f"{line[0].upper()} ▸ {line[1]}"
            c.itemconfig(body, text=text, fill=FG if line else DIM)
            while c.bbox(body)[3] > room and len(text) > 40:  # short panel: shortened, never cut mid-line
                text = text[:int(len(text) * 0.8)].rsplit(" ", 1)[0] + "…"
                c.itemconfig(body, text=text)
        for k in range(len(chips)):
            c.tag_bind(f"chip{k}", "<Enter>", lambda _e, k=k: show(k, True))
            c.tag_bind(f"chip{k}", "<Leave>", lambda _e: show(cur))
        show(cur)
        return c.bbox(body)[3] + P(6)


class LevelPanel(SidePanel):
    """Max mode: the leveling plan as the big chips (one row), in the room under the item builds; the side
    panel keeps the build cards, HOLD and gold lines."""

    def __init__(self, parent, width, height, s):
        super().__init__(parent, width, height, s, on_pin=lambda _name: None)

    def update(self, msg):
        board = msg.get("board") or {}
        args = (board.get("plan"), msg.get("level"), msg.get("round"), board.get("plan_bm"), msg.get("streak"))
        stages = (board.get("guide") or {}).get("by_stage")  # the guide's advice per stage, under the chips
        sig = json.dumps([args, stages, self._height()], default=str)
        self._last = msg
        if sig == self._sig:
            return
        self._sig = sig
        self.c.delete("all")
        self._bottom = self._height()
        if not self._plan(args[0], 0, *args[1:], stages=stages)[1]:
            self._strip(*args)  # no room for the chips: the one-line version


class ShopPopup:
    """A popup over the game window: for each unit in your shop worth buying (your pinned build's units, else
    the safe buys), a guide-sized flashing hexagon with the champion and "BUY name" — in a row in the middle of
    your board, where you're looking (config "shop_popup_at": "center"), or stacked over the player list on the
    right ("right"). Only the hexagons show (the rest of the window is see-through), clicks go through (you can
    still drag units and scout), and it's hidden from screen capture so the readers never see it. Needs TFT in
    borderless / windowed mode."""
    KEY = "#010203"  # see-through colour
    AREAS = {"center": (0.28, 0.33, 0.72, 0.57),   # the middle of your board, in a row (fractions of the game window)
             "right": (0.862, 0.12, 0.985, 0.78)}  # over the player list, stacked

    def __init__(self, root, at="center"):
        self.row = at != "right"
        self.AREA = self.AREAS["right" if at == "right" else "center"]
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-transparentcolor", self.KEY)
        self.top.configure(bg=self.KEY)
        self.c = tk.Canvas(self.top, bg=self.KEY, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.icons = IconCache()
        self.champs = load_set()["champions"]
        self.top.withdraw()
        self._styled, self._sig, self._on = False, None, False
        self._flash()

    def _style(self):
        """Click-through, no taskbar entry, never takes focus, and invisible to screen capture."""
        user32 = ctypes.windll.user32
        self.top.update_idletasks()
        hwnd = user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
        GWL_EXSTYLE = -20
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        self._styled = True

    def show(self, slots, buy, hide=False):
        from .capture import game_rect
        rect = None if hide else game_rect()
        want = set(buy or ())
        counts = Counter(u for u in slots or [] if u and u in want)
        picks = list(dict.fromkeys(u for u in slots or [] if u in counts))  # shop order, each unit once
        if not rect or not picks:
            self.top.withdraw()
            self._sig = None
            return
        W, H = rect["width"], rect["height"]
        x0, x1 = rect["left"] + int(W * self.AREA[0]), rect["left"] + int(W * self.AREA[2])
        y0, y1 = rect["top"] + int(H * self.AREA[1]), rect["top"] + int(H * self.AREA[3])
        sig = (x0, y0, x1, y1, tuple(picks), tuple(counts[u] for u in picks))
        if sig == self._sig:
            return
        self._sig = sig
        self.top.geometry(f"{x1 - x0}x{y1 - y0}+{x0}+{y0}")
        c = self.c
        c.delete("all")
        # guide-sized hexagons (about the overlay board's), smaller only if many: a centred row in the middle of
        # the board, or stacked from the top over the player list
        if self.row:
            size = int(min(H * 0.13, (y1 - y0) * 0.95, (x1 - x0) / len(picks) / 1.08))
        else:
            size = int(min((x1 - x0) * 0.62, H * 0.13, (y1 - y0) / len(picks) * 0.9))
        r = size / 2
        for k, unit in enumerate(picks):
            if self.row:
                cx = (x1 - x0) / 2 + (k - (len(picks) - 1) / 2) * size * 1.08
                cy = (y1 - y0) / 2
            else:
                cx, cy = (x1 - x0) / 2, r * 1.02 + k * size * 1.08
            pts = []
            for i in range(6):  # pointy-top hexagon
                a = math.radians(60 * i - 90)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
            c.create_polygon(pts, fill=CARD, outline=GREEN, width=max(4, int(r * 0.12)), tags=("ring",))
            img = self.icons.get(self.champs.get(unit, {}).get("icon"), int(size * 0.66), folder="champ_icons")
            if img:
                c.create_image(cx, cy - r * 0.06, image=img)
            label = f"BUY {unit}" + (f" ×{counts[unit]}" if counts[unit] > 1 else "")
            t = c.create_text(cx, cy + r * 0.6, text=label, fill="#111", font=("Segoe UI", max(9, int(r * 0.2)), "bold"))
            bx0, by0, bx1, by1 = c.bbox(t)
            c.tag_lower(c.create_rectangle(bx0 - 6, by0 - 1, bx1 + 6, by1 + 1, fill=GREEN, outline="", tags=("badge",)), t)
        self.top.deiconify()
        self.top.lift()
        if not self._styled:
            self._style()

    def _flash(self):
        """Ring and badge blink green / gold while the popup is up."""
        try:
            if self._sig:
                self._on = not self._on
                self.c.itemconfigure("ring", outline=ACCENT if self._on else GREEN)
                self.c.itemconfigure("badge", fill=ACCENT if self._on else GREEN)
        finally:
            self.top.after(400, self._flash)


class Overlay:
    def __init__(self, source=None, capturable=False):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.worker = Worker(self.cfg, self.q, source)
        self.capturable = capturable
        self._last_sig = None
        self._last_msg = None
        self.comps = load_comps()
        if self.cfg.get("locked") and self.cfg["locked"] not in {c["name"] for c in self.comps}:
            self.cfg["locked"] = self.worker.locked = None  # pinned comp no longer exists (renamed/removed)
            save_config(self.cfg)
        self.maximized, self.s = False, 1.0

        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.cfg["alpha"])
        root.configure(bg=BG)
        # explicit WxH: Tk stops auto-sizing to content, so the window never resizes
        root.geometry(f"{self.cfg['width']}x{self.cfg['height']}+{self.cfg['x']}+{self.cfg['y']}")
        # BUY hexagons in the middle of your board (live only; "shop_popup": false in config.json turns them off,
        # "shop_popup_at": "right" puts them back over the player list)
        self.popup = ShopPopup(root, self.cfg.get("shop_popup_at", "center"))             if source is None and self.cfg.get("shop_popup", True) else None

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=8, pady=(6, 2))
        title = tk.Label(bar, text="TFT Meta", fg=ACCENT, bg=BG, font=("Segoe UI", 10, "bold"))
        title.pack(side="left")
        tk.Button(bar, text="✕", command=self.quit, **self._btn()).pack(side="right")
        self.max_btn = tk.Button(bar, text="⤢", command=self.toggle_max, **self._btn())
        self.max_btn.pack(side="right", padx=(0, 2))
        self.pause_btn = tk.Button(bar, text="❚❚", command=self.toggle_pause, **self._btn())
        self.pause_btn.pack(side="right")
        tk.Button(bar, text="⟳", command=self.worker.refresh.set, **self._btn()).pack(side="right")
        # ⚑: something's wrong — save this screen and what the overlay read (tests/fixtures/reports)
        tk.Button(bar, text="⚑", command=self.worker.report.set, **self._btn()).pack(side="right")
        # 📖: the whole guide for your build (pinned, else the one on the board), in its own window
        tk.Button(bar, text="📖", command=self.show_guide, **self._btn()).pack(side="right")
        self.guide_win = None
        # visible only while a build is pinned: one click back to auto
        self.unpin_btn = tk.Button(bar, text="", command=lambda: self.lock(None), **self._btn())
        self.unpin_btn.config(fg=ACCENT)
        self.unpin_btn.pack(side="right")
        self.build_btn = tk.Menubutton(bar, text="", width=16, anchor="e", relief="flat", bg=BG, fg=FG,
                                       activebackground=CARD, activeforeground=FG,
                                       font=("Segoe UI", 8), cursor="hand2")
        menu = self.menu = tk.Menu(self.build_btn, tearoff=0, bg=CARD, fg=FG, activebackground=ACCENT,
                                   activeforeground="#111", font=("Segoe UI", 9))
        self._fill_menu()
        self.build_btn["menu"] = menu
        self.build_btn.pack(side="right", padx=4)
        self._show_lock()
        # title-bar widgets and their base font sizes, rescaled when maximising
        self.bar_fonts = [(title, 10, "bold"), (self.build_btn, 8, None), (self.max_btn, 9, None),
                          (self.pause_btn, 9, None), (menu, 9, None)]
        self.bar_fonts += [(w, 9, None) for w in bar.winfo_children()
                           if isinstance(w, tk.Button) and w not in (self.max_btn, self.pause_btn)]
        for w in (bar, title):  # drag to move
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Double-Button-1>", lambda _e: self.toggle_max())

        # status first, pinned to the bottom, so overflowing content clips instead of hiding it
        self.status = tk.Label(root, text="Starting OCR…", fg=DIM, bg=BG, font=("Segoe UI", 8), anchor="w")
        self.status.pack(side="bottom", fill="x", padx=8, pady=(2, 6))
        self.body = None
        if self.cfg.get("maximized") and self.cfg.get("max_at"):
            self._maximize(*self.cfg["max_at"], save=False)
        else:
            self._restore(save=False)

    def _fill_menu(self):
        """Build menu: Auto, then every build by tier (refilled when the builds update)."""
        self.menu.delete(0, "end")
        self.menu.add_command(label="Auto (best fit)", command=lambda: self.lock(None))
        self.menu.add_separator()
        for comp in sorted(self.comps, key=tier_rank):
            need = needs_text(comp, unmet(comp))  # what it needs before it's worth playing, in the list too
            need = (need if len(need) <= 48 else need[:46] + "…") if need else ""
            self.menu.add_command(label=f"{comp['tier']:<7} {comp.get('guide_name') or comp['name']}"
                                        + (f"   — needs {need}" if need else ""),
                                  command=lambda n=comp["name"]: self.lock(n))

    @staticmethod
    def _btn():
        return dict(fg=DIM, bg=BG, activebackground=BG, activeforeground=FG, bd=0,
                    font=("Segoe UI", 9), cursor="hand2")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        if not self.maximized:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, _):
        if not self.maximized:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            save_config(self.cfg)

    # ---- maximise: fill the monitor the window is on, bigger two-column layout -------------
    def toggle_max(self):
        if self.maximized:
            self._restore()
        else:
            self._maximize(self.root.winfo_x() + self.root.winfo_width() // 2,
                           self.root.winfo_y() + self.root.winfo_height() // 2)

    def _maximize(self, x, y, save=True):
        left, top, w, h = monitor_rect(x, y)
        self.maximized = True
        self.root.geometry(f"{w}x{h}+{left}+{top}")
        self.root.attributes("-alpha", 1.0)
        self.max_btn.config(text="⤡")
        self._build_body(w, h)  # sized from the monitor itself, not from Tk's (late) geometry
        if save:
            self.cfg["maximized"], self.cfg["max_at"] = True, [x, y]
            save_config(self.cfg)

    def _restore(self, save=True):
        self.maximized = False
        w, h = self.cfg["width"], self.cfg["height"]
        x, y = clamp_to_screen(self.cfg["x"], self.cfg["y"], w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.max_btn.config(text="⤢")
        self._build_body(w, h)
        if save:
            self.cfg["maximized"] = False
            save_config(self.cfg)

    def _build_body(self, w, h):
        """(Re)create the content area for the current size. Compact: one column. Maximised:
        item panel + reads on the left, comp cards on the right, everything scaled up."""
        if self.body is not None:
            self.body.destroy()
        self._body_size = (w, h)
        self.board_panel = None
        self.side = None
        self.levels = None
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=8)
        if self.maximized:
            # the board across the full width, as big as the height allows; underneath: the guide's
            # item builds (left) and pick / pivot / plan (right)
            gap = 24
            bottom_h = max(260, int(h * 0.28))  # 3 build cards + the leveling and gold lines under them
            top_h = h - 80 - bottom_h
            items_w = int((w - 16 - gap) * 0.6)
            right_w = w - 16 - gap - items_w
            item_s = max(1.0, min(items_w / 640, (bottom_h - 10) / PANEL_H, 2.0))
            self.s = item_s
            top = tk.Frame(self.body, bg=BG)
            top.pack(side="top", fill="x", anchor="n")
            self.board_panel = BoardPanel(top, w - 16, top_h, max(1.0, min((w - 16) / 1000, top_h / 520)))
            bottom = tk.Frame(self.body, bg=BG)
            bottom.pack(side="top", fill="both", expand=True, anchor="n")
            left = tk.Frame(bottom, bg=BG, width=items_w)
            left.pack(side="left", fill="y", anchor="n")
            right = tk.Frame(bottom, bg=BG)
            right.pack(side="left", fill="both", expand=True, padx=(gap, 0), anchor="n")
            self.panel = ItemPanel(left, items_w, item_s, self.worker.mark_item)
            side_s = max(1.0, min(right_w / 560, 1.4))
            # the leveling chips in the room under the item builds; the side panel keeps cards + gold
            self.levels = LevelPanel(left, items_w, bottom_h - self.panel.c.winfo_reqheight() - 12, side_s)
            self.side = SidePanel(right, right_w, bottom_h, side_s, self.lock,
                                  self.worker.have, self.worker.dismiss_offer, levels=False)
        else:
            # compact: board first (it's what you look at), then items, advice, alternatives
            self.s = 1.0
            bw = w - 16
            self.board_panel = BoardPanel(self.body, bw, board_height(bw) + 100, 1.0)
            self.panel = ItemPanel(self.body, bw, 1.0, self.worker.mark_item)
            self.side = SidePanel(self.body, bw, 400, 0.85, self.lock, self.worker.have, self.worker.dismiss_offer)
        k = min(self.s, 1.8)
        for widget, size, weight in self.bar_fonts:
            widget.config(font=("Segoe UI", int(round(size * k)), *([weight] if weight else [])))
        self.build_btn.config(width=0 if self.maximized else 16)  # full build name when there's room
        self.status.config(font=("Segoe UI", int(round(8 * k))))
        if self._last_msg:
            self.render(self._last_msg)

    def lock(self, name):
        """Pin a comp as 'my build' (None = auto). Item panel and advice follow it.
        Pinning the already-pinned comp again (e.g. clicking its card) unpins it."""
        if name is not None and name == self.cfg.get("locked"):
            name = None
        self.cfg["locked"] = self.worker.locked = name
        self.worker.relock.set()
        save_config(self.cfg)
        self._show_lock()
        self.status.config(text=f"Build locked: {name}" if name else "Build: auto")

    def _show_lock(self):
        name = self.cfg.get("locked")
        guide = next((c.get("guide_name") for c in self.comps if c["name"] == name), None) if name else None
        short = (guide or name.replace(" — Standard", "")) if name else None
        self.build_btn.config(text=(f"📌 {short}" if name else "Build: Auto") + " ▾",
                              fg=ACCENT if name else FG)
        self.unpin_btn.config(text="✕ unpin" if name else "", state="normal" if name else "disabled")

    def show_guide(self):
        """The build's whole guide in a window of its own (scrolls): tier and ranked results, what it needs,
        the gameplan and every stage, tips, item holders, items, augments, leveling, links."""
        name = self.cfg.get("locked") or ((self._last_msg or {}).get("board") or {}).get("comp")
        comp = next((c for c in self.comps if c["name"] == name), None)
        if not comp:
            self.status.config(text="Pick a build from the build menu first")
            return
        if self.guide_win is not None and self.guide_win.winfo_exists():
            self.guide_win.destroy()
        win = self.guide_win = tk.Toplevel(self.root)
        win.title(f"Guide — {comp.get('guide_name') or comp['name']}")
        win.configure(bg=BG)
        win.attributes("-topmost", True)
        x, y = clamp_to_screen(self.root.winfo_x() + 40, self.root.winfo_y() + 40, 620, 760)
        win.geometry(f"620x760+{x}+{y}")
        bar = tk.Scrollbar(win)
        bar.pack(side="right", fill="y")
        t = tk.Text(win, wrap="word", bg=BG, fg=FG, insertbackground=FG, bd=0, padx=14, pady=10,
                    font=("Segoe UI", 10), yscrollcommand=bar.set, spacing1=2, spacing3=2)
        t.pack(fill="both", expand=True)
        bar.config(command=t.yview)
        t.tag_config("h1", foreground=ACCENT, font=("Segoe UI", 15, "bold"))
        t.tag_config("h2", foreground=DIM, font=("Segoe UI", 9, "bold"), spacing1=12)
        t.tag_config("dim", foreground=DIM)
        t.tag_config("warn", foreground="#ffb347", font=("Segoe UI", 10, "bold"))

        def add(text, *tags):
            t.insert("end", text + "\n", tags)

        def head(text):
            add(text.upper(), "h2")
        h = comp.get("howto") or {}
        add(comp.get("guide_name") or comp["name"], "h1")
        add(f"{comp.get('tier', '?')} tier  ·  {comp.get('guide', '')}" +
            (f"  ·  {comp['badge']}" if comp.get("badge") else ""), "dim")
        st = comp.get("stats")
        if st:
            add(f"Ranked ({st['source']}, {st['comp']}): top 4 {st['top4']:.1f}%, avg {st['avg']:.2f}, "
                f"win {st['win']:.1f}%", "dim")
        if comp.get("notes"):
            add(comp["notes"], "dim")
        need = needs_text(comp, unmet(comp))
        if need:
            add(f"Needs: {need}", "warn")
        if comp.get("conditions"):
            add(f"Conditions (guide): {comp['conditions']}", "dim")
        if h.get("gameplan"):
            head("Gameplan")
            add(h["gameplan"])
        for label, text in h.get("stages") or []:
            head(label)
            add(text)
        if h.get("holders") or h.get("emblem"):
            head("Item holders / emblem")
            if h.get("holders"):
                add(f"Item holders: {h['holders']}")
            if h.get("emblem"):
                add(f"Best emblem / spatula holder: {h['emblem']}")
        if h.get("tips"):
            head("Tips")
            for tip in h["tips"]:
                add("• " + tip)
        head("Items")
        if h.get("items"):
            for unit, text, *_ in h["items"]:
                add(f"{unit}: {text}")
        else:
            for unit, its in (comp.get("carries") or {}).items():
                if its:
                    add(f"{unit}: {', '.join(its)}")
        if comp.get("early"):
            head("Early opener")
            add(", ".join(e["unit"] + (f" ({', '.join(e['items'])})" if e.get("items") else "") for e in comp["early"]))
        augs = [f"{a}: {b}" for a, b in h.get("augments") or []] + list(comp.get("augments") or [])
        if augs or comp.get("augments_tip"):
            head("Augments")
            for a in augs:
                add(a)
            if comp.get("augments_tip"):
                add(comp["augments_tip"], "dim")
        rows = (comp.get("plan_bm") or {}).get("rows") or {}
        if rows or (comp.get("plan_bm") or {}).get("text"):
            head("Leveling (bunnymuffins)")
            for streak, steps in rows.items():
                add(f"{streak}: " + "  ·  ".join(f"{rnd} Lv {lv}" + (f" ({note})" if note else "")
                                                   for lv, rnd, note in steps))
            if comp["plan_bm"].get("text"):
                add(comp["plan_bm"]["text"])
        if comp.get("source"):
            head("Source")
            add(comp["source"], "dim")
        t.config(state="disabled")
        win.bind("<Escape>", lambda _e: win.destroy())
        if not self.capturable:  # like the overlay: the game reader never sees it, nor do recordings
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)

    def toggle_pause(self):
        if self.worker.paused.is_set():
            self.worker.paused.clear()
            self.pause_btn.config(text="❚❚")
        else:
            self.worker.paused.set()
            self.pause_btn.config(text="▶")
            self.status.config(text="Paused")

    def quit(self):
        self.root.destroy()

    def hide_from_capture(self):
        """Exclude this window from screen capture so OCR never reads the overlay itself
        (Windows 10 2004+). Also keeps it out of screenshots/recordings."""
        WDA_EXCLUDEFROMCAPTURE = 0x11
        self.root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)

    def render(self, msg):
        if self.status.cget("text") != msg["status"]:
            self.status.config(text=msg["status"])
        if msg.get("restart"):  # the game data was just updated for a new patch: start again on it
            import os
            import subprocess
            save_config(self.cfg)
            subprocess.Popen([sys.executable] + sys.argv, cwd=str(ROOT))
            self.root.destroy()
            os._exit(0)
        if msg.get("guides_updated"):
            self.comps = load_comps()
            self._fill_menu()
        if msg.get("unlock"):
            self.cfg["locked"] = None
            save_config(self.cfg)
            self._show_lock()
        if msg.get("preview_clear"):  # unpinned with no game running: back to "Waiting for game…"
            self._last_msg = None
            self._build_body(*self._body_size)
        if "results" not in msg:
            return
        self._last_msg = msg
        self.panel.update(msg["panel"])
        if getattr(self, "board_panel", None) and msg.get("board"):
            self.board_panel.update(msg["board"])
        if getattr(self, "side", None):
            self.side.update(msg)
        if getattr(self, "levels", None):
            self.levels.update(msg)
        if self.popup:
            self.popup.show(msg.get("slots"), msg.get("buy_units"), hide=self.worker.paused.is_set())

    def poll(self):
        try:
            self._poll()
        finally:  # an error in one update must not stop the updates
            self.root.after(100, self.poll)

    def _builds_changed(self):
        """Every ~5 s: a build file changed on disk (your custom_comps.json / comp_overrides.json edits, a sync)
        -> reload the builds so the build menu and 📖 have them without a restart."""
        self._tick = getattr(self, "_tick", 0) + 1
        if self._tick % 50:
            return
        files = [DATA / "comps.json", DATA / "custom_comps.json", DATA / "comp_overrides.json", DATA / STATS_FILE] + \
            [DATA / f for f in OTHER_GUIDES]
        m = [f.stat().st_mtime if f.exists() else 0 for f in files]
        if getattr(self, "_builds_m", None) not in (None, m):
            self.comps = load_comps()
            self._fill_menu()
            self._show_lock()
        self._builds_m = m

    def _poll(self):
        self._builds_changed()
        msgs = []
        while not self.q.empty():
            msgs.append(self.q.get_nowait())
        # only the newest full update is drawn, but a status line behind it never replaces it, and no status
        # message (unlock, guides updated, restart, …) is dropped
        last_full = max((i for i, m in enumerate(msgs) if "results" in m), default=-1)
        shop = None
        for i, m in enumerate(msgs):
            if "results" in m:
                if i == last_full:
                    self.render(m)
            elif "shop" in m:
                if i > last_full:  # a full update already has the current shop
                    shop = m
            else:
                self.render(m)
        if shop is not None and getattr(self, "board_panel", None):
            self.board_panel.set_shop(shop["shop"])
        if shop is not None and self.popup:
            self.popup.show(shop.get("slots"), shop.get("buy_units"), hide=self.worker.paused.is_set())

    def animate(self):
        try:
            self.panel.tick()
            if getattr(self, "board_panel", None):
                self.board_panel.tick()
        finally:
            self.root.after(50, self.animate)

    def update_guides(self):
        """Startup: game data for a new patch first (Community Dragon; after an update the overlay restarts
        on it), then bring the builds in line with bunnymuffins.lol/meta and TFT Academy's tier list, then
        fetch SeeMeta's ranked results that cap their tiers (background; the overlay works meanwhile and
        reloads the builds after each)."""
        import os
        import subprocess

        def go():
            env = dict(os.environ, PYTHONIOENCODING="utf8")  # else Windows pipes are cp1252: "—" breaks the read
            # first the game data (a new patch: champions, items, augments), then the guides built on it
            for guide, tool in (("Community Dragon", "update_game_data.py"), ("bunnymuffins", "import_teamfight.py"),
                                ("TFT Academy", "import_tftacademy.py"), ("SeeMeta", "import_seemeta.py")):
                self.q.put({"status": f"Checking {guide} for updates…"})
                try:
                    r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--sync"],
                                       capture_output=True, text=True, encoding="utf8", timeout=900, env=env,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("SYNC ")), None)
                    if not line:
                        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:] or "no result")
                    res = json.loads(line[5:])
                    if res.get("updated"):  # new patch data: the reader and builds load it at start, so restart
                        self.q.put({"status": f"New patch: {res['changed'][0]} — restarting…", "restart": True})
                        return
                    bits = [f"{len(res[k])} {k}" for k in ("added", "changed", "removed") if res[k]]
                    msg = (f"Builds updated from {guide}: " + ", ".join(bits)) if bits else \
                        f"Builds are up to date with {guide}"
                    self.q.put({"status": msg, "guides_updated": True})
                except Exception as e:  # offline etc.: keep the builds we have
                    self.q.put({"status": f"{guide} update check failed ({e}); using saved builds"})
        threading.Thread(target=go, daemon=True).start()

    def run(self):
        if not self.capturable:
            self.hide_from_capture()
        if self.worker.source is None:  # live: check the guides for updates
            self.update_guides()
        self.worker.start()
        self.poll()
        self.animate()
        self.root.mainloop()


def be_nice():
    """Keep out of the game's way: below-normal CPU priority, single-threaded OpenCV."""
    BELOW_NORMAL_PRIORITY_CLASS = 0x4000
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p  # 64-bit pseudo-handle; default int mangles it
    k32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    import cv2
    cv2.setNumThreads(1)


def main():
    be_nice()
    ap = argparse.ArgumentParser(description="TFT meta overlay")
    ap.add_argument("--demo", metavar="IMAGE", help="read a saved screenshot instead of the live game")
    ap.add_argument("--capturable", action="store_true",
                    help="don't hide the overlay from screenshots (for checking its look)")
    ap.add_argument("--force-carousel", action="store_true", help=argparse.SUPPRESS)  # dev: test pick UI
    ap.add_argument("--log-only", action="store_true",
                    help="no window: just record shops for tools/shop_report.py")
    args = ap.parse_args()
    if args.log_only:
        cfg = load_config()
        cfg["log_only"], cfg["shop_log"] = True, True
        q = queue.Queue()
        print("Logging shops (no window). Ctrl+C to stop; then run: py tools/shop_report.py", flush=True)
        try:
            Worker(cfg, q).run()
        except KeyboardInterrupt:
            pass
        return
    overlay = Overlay(FileSource(args.demo) if args.demo else None, capturable=args.capturable)
    overlay.cfg["force_carousel"] = args.force_carousel
    overlay.run()
