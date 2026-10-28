import json
import re
from pathlib import Path

from .paths import DATA  # noqa: E402  (source folder, or next to TFTMeta.exe)


def load_set():
    """{'traits': {name: [breakpoints]}, 'champions': {name: {'cost', 'traits'}}}"""
    return json.loads((DATA / "set.json").read_text(encoding="utf8"))


def load_item_names():
    """Every offerable item name (standard, artifacts, emblems) for OCR matching."""
    return json.loads((DATA / "item_names.json").read_text(encoding="utf8"))


def load_item_stats():
    """{item name: [stat tags]} e.g. "Luden's Tempest": ["AD", "AP"]"""
    path = DATA / "item_stats.json"
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else {}


def load_special():
    """{artifact name: icon file}"""
    return json.loads((DATA / "special.json").read_text(encoding="utf8"))


def load_augment_names():
    """This set's augment names, for reading the augment pick screen."""
    path = DATA / "augment_names.json"
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else []


_TIERS = ["S", "A", "B", "C", "D", "Special"]


def parse_leveling(text):
    """bunnymuffins' Leveling Pattern -> {"style": "3 Cost Reroll", "rows": {"Lose Streak":
    [(level, round, note)], ...}, "text": the advice that follows (or the whole thing when there are
    no rows, e.g. 1-cost rerolls: "Do not press the level button until you 3 star...")}."""
    if not text:
        return None
    m = re.match(r"(\d Cost Reroll|Fast \d(?:/\d)?)\s*", text)
    style = m.group(1) if m else ""
    rest = text[m.end():] if m else text
    rows = {}
    pat = re.compile(r"(Win Streak|Lose Streak|Mixed Streak|Other)\s+Round\s+(.*?)\s+Level\s+((?:\d+\s*)+)")
    last = 0
    for r in pat.finditer(rest):
        name, rounds, levels = r.group(1), r.group(2).split(), [int(x) for x in r.group(3).split()]
        entries, i = [], 0
        while i < len(rounds):
            rnd, note = rounds[i], ""
            if i + 2 < len(rounds) and rounds[i + 1] == "or":  # "3-5 or 4-1"
                rnd, i = f"{rnd} or {rounds[i + 2]}", i + 2
            if i + 1 < len(rounds) and rounds[i + 1].startswith("("):  # "(40g+)"
                note, i = rounds[i + 1].strip("()"), i + 1
            entries.append((rnd.replace("+", "-1+") if re.fullmatch(r"\d\+", rnd) else rnd, note))
            i += 1
        rows[name] = [(lv, rnd, note) for (rnd, note), lv in zip(entries, levels)]
        last = r.end()
    tail = rest[last:].replace("Mixed Streak", "").strip()
    return {"style": style, "rows": rows, "text": tail}


def condition_tokens(text):
    """bunnymuffins' Conditions text -> tokens any one of which meets the requirement:
    'emblem:Invoker' / 'emblem:any', 'forge', 'aug:Consuming Flora', 'unit:Kha'Zix',
    'items:4' (finished items held), 'gold:50', 'bows:2'."""
    if not text:
        return []
    # what makes the build better isn't what it needs: "Invoker Emblem Ahri bumps this up to S tier",
    # "Artifacts and Emblems bump this up a tier", "S tier with Executioner Emblem", "Great with emblems"
    text = re.sub(r"[A-Z][\w']*(?:\s+(?:and|or|[A-Z][\w']*))*\s+(?i:bumps?\s+this\s+up\s+(?:to\s+\w+\s+tier|a\s+tier))"
                  r"|(?i:goes\s+up\s+a\s+tier\s+with)(?:\s+[a-z][\w']*)+"
                  r"|S\s+[Tt]ier\s+with(?:\s+[A-Z][\w']*){1,2}"
                  r"|(?i:(?:much\s+stronger|great)\s+with\s+\w+)", " ", text)
    low = text.lower()
    set_data = load_set()
    out = []
    traits = [t for t in set_data["traits"] if re.search(rf"\b{re.escape(t.lower())}\b", low)]
    if "emblem" in low:
        named = [t for t in traits if re.search(rf"\b{re.escape(t.lower())}\b(?:\s+and\s+\w+)?\s+emblem", low)
                 or re.search(rf"\b{re.escape(t.lower())}\s+and\s+\w+\s+emblems", low)]
        out += [f"emblem:{t}" for t in named] or ["emblem:any"]
    if "artifact" in low:
        out.append("forge")
    out += [f"aug:{a}" for a in load_augment_names() if a.lower() in low]
    # a champion the comp must have early ("2-1 to 2-3 Kha'Zix", "Early LeBlanc and Ornn", "BIS Kha'Zix")
    for u in set_data["champions"]:
        if re.search(rf"\b{re.escape(u.lower())}\b", low) and re.search(
                rf"(early|bis|\d-\d to \d-\d)[^.]{{0,40}}\b{re.escape(u.lower())}\b", low):
            out.append(f"unit:{u}")
    if re.search(r"lots of items|2 sets of carry items|lots of item", low):
        out.append("items:4")
    if "lots of econ" in low:
        out.append("gold:50")
    if "a lot of bows" in low:
        out.append("bows:2")
    return list(dict.fromkeys(out))


STATS_FILE = "seemeta.json"  # real ranked results (tools/import_seemeta.py): they cap every guide's letter
# real top-4 rate -> the best tier a build can show. 4 of 8 players make top 4: 50% is a coin flip
TOP4_TIER = [(56, "S"), (52, "A"), (48, "B"), (44, "C"), (0, "D")]
UNPROVEN_TIER = "B"  # a build ranked play doesn't show (niche, emblem builds): not proven S/A
MIN_SHARED = 5       # units in common with a SeeMeta comp (of its 9) to count as that comp


def real_results(comp, stats):
    """The SeeMeta comp this build is (most shared units, 5+; a tie goes to the better average), or None."""
    units = set(comp.get("units") or [])
    best = max(stats, key=lambda s: (len(units & set(s["units"])), -s["avg"]), default=None)
    return best if best and len(units & set(best["units"])) >= MIN_SHARED else None


# other guides' builds (their own syncs), next to bunnymuffins'. On a tie the guide listed first keeps the build.
# (tftactics.gg was tried and dropped: most of its S/A builds don't hold up in ranked results; TFT Academy was
# dropped on 2026-10-24, your call: too many gimmicky capped / emblem boards. tools/import_tftacademy.py still works.)
OTHER_GUIDES = []


def _main(c):
    return c.get("main") or next(iter(c.get("carries") or {}), None)


def _overlap(a, b):
    """Share of units two builds have in common (0..1)."""
    x, y = set(a.get("units") or []), set(b.get("units") or [])
    return len(x & y) / len(x | y) if x and y else 0


def _same_build(a, b):
    """Two guides' versions of one build: mostly the same units, and one's main carry is a carry in the
    other (Solar Karma vs Solar Yunara: same board, either carry)."""
    def carrying(c):
        return {u for u, its in (c.get("carries") or {}).items() if its}

    def needs_special(c):  # tagged by its guide as needing an emblem / augment: a variant of its own
        return c.get("special_items_optional") is False
    return (needs_special(a) == needs_special(b) and _overlap(a, b) >= 0.6
            and (_main(a) in carrying(b) or _main(b) in carrying(a)))


def load_comps():
    """comps.json with data/comp_overrides.json applied: `max_tier` caps a comp's tier (data tiers
    can't see emblem/augment dependence) and `needs` says what it depends on. Other guides' builds
    (OTHER_GUIDES) are merged in; the same build from two guides shows once."""
    path = DATA / "comps.json"  # written by the startup sync; a fresh copy of the project has none yet
    store = json.loads(path.read_text(encoding="utf8")) if path.exists() else {"comps": []}
    comps = store["comps"]
    for c in comps:
        c["guide"] = "bunnymuffins"
    for f in OTHER_GUIDES:
        if (DATA / f).exists():
            comps += json.loads((DATA / f).read_text(encoding="utf8"))["comps"]
    custom = DATA / "custom_comps.json"  # comps you added yourself; the importers never touch this file
    if custom.exists():  # same treatment as the guides' (name, badge, 3★ order); one of yours replaces a same-named one
        mine = json.loads(custom.read_text(encoding="utf-8-sig"))["comps"]
        for c in mine:
            c["guide"] = "mine"
        names = {c["name"] for c in mine}
        comps = [c for c in comps if c["name"] not in names] + mine
    aliases = store.get("_aliases") or {}
    starred, conds = store.get("_starred") or {}, store.get("_conditions") or {}
    leveling = store.get("_leveling") or {}
    howto = store.get("_howto") or {}
    for c in comps:  # the name bunnymuffins uses ("Defender Cassiopeia"), + the variant if not Standard
        base, _, variant = c["name"].partition(" — ")
        bm = c["guide"] == "bunnymuffins"  # its names, conditions and leveling are about its own builds
        c["guide_tier"] = c.get("tier")  # the guide's own letter, before any cap
        c["guide_name"] = (aliases.get(base, base) if bm else base) + \
            (f" — {variant}" if variant and variant != "Standard" else "")
        c.setdefault("main", next(iter(c.get("carries") or {}), None))  # before reroll builds reorder them
        # bunnymuffins '*' = a big requirement; its "Conditions" say which (any one will do)
        c["conditions"] = conds.get(base, "") if bm else ""
        c["guide_req"] = condition_tokens(c["conditions"]) if bm and starred.get(base) else []
        # reroll builds are about their 3-star targets (the guide board's 3★ units): those come first
        # in the item builds and item priority — Ezreal's 3 items don't make Kha'Zix Reroll a 4-cost comp
        stars = [b["unit"] for b in c.get("board") or [] if b.get("three")]
        style = (c.get("plan") or {}).get("style", "")
        if "reroll" in style.lower() and stars:
            order = list(c["carries"])
            c["carries"] = dict(sorted(c["carries"].items(), key=lambda kv: (kv[0] not in stars, order.index(kv[0]))))
        c["stars"] = stars
        c["plan_bm"] = parse_leveling(leveling.get(base, "") if bm else "")  # the guide's own leveling pattern
        # how to play it, stage by stage, from its guide: bunnymuffins' card, TFT Academy's stage tips
        if bm:
            c["howto"] = howto.get(base)
        elif c.get("tips") and not c.get("howto"):
            c["howto"] = {"stages": [list(map(str.strip, t.split(":", 1))) for t in c["tips"] if ":" in t],
                          "tips": [c["augments_tip"]] if c.get("augments_tip") else []}
        bm = (c.get("plan_bm") or {}).get("style", "")
        if bm:  # the guide author's build type wins over my template's guess
            style = bm.replace(" Cost Reroll", "-cost reroll").replace("Fast 8/9", "level 8 / fast 9")
        cost = re.search(r"(\d)-cost", style)
        roll_hint = {"1": "roll at Lv 4", "2": "roll at Lv 6", "3": "roll at Lv 7"}
        c["badge"] = (f"{cost.group(1)}-COST REROLL · {roll_hint.get(cost.group(1), '')}"
                      if "reroll" in style.lower() and cost else "FAST 8/9 · 4-costs at Lv 8" if "level 8 / fast 9" in style
                      else "FAST 9" if "fast 9" in style.lower()
                      else "FAST 8 · 4-costs at Lv 8" if "level 8" in style.lower() else "")
    # a comp is only offered when it has a real guide: board positions or item builds. SeeMeta's
    # unit-only lists ("Rival Rengar") only feed the tier stats
    comps = [c for c in comps if c.get("board") or any((c.get("carries") or {}).values())]
    path = DATA / "comp_overrides.json"
    over = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    for c in comps:
        o = over.get(c["name"].split(" — ")[0].strip())
        if not o:
            continue
        cap = o.get("max_tier")
        if cap in _TIERS and c.get("tier") in _TIERS and _TIERS.index(c["tier"]) < _TIERS.index(cap):
            c["data_tier"], c["tier"] = c["tier"], cap
        if o.get("needs"):
            c["needs"] = o["needs"]
        if o.get("hide"):  # your call: never suggest it or list it (still used to read what opponents play)
            c["hidden"] = True
        if o.get("ignore_conditions"):  # your call: the guide's '*' condition is easy enough not to cap it at B
            c["guide_req"] = []
        if o.get("note"):  # your own read on the comp, shown with its notes
            c["notes"] = (o["note"] + " " + c.get("notes", "")).strip()
    # real ranked results cap every guide's letter: a build shows S/A only when it finishes top 4 that often.
    # Your own builds (custom_comps.json) are left alone; no file yet (offline): the guides' letters as they are
    path = DATA / STATS_FILE
    stats = json.loads(path.read_text(encoding="utf8"))["comps"] if path.exists() else []
    for c in comps if stats else []:
        if c["guide"] == "mine":
            continue
        s = real_results(c, stats)
        cap = next(t for p, t in TOP4_TIER if s["top4"] >= p) if s else UNPROVEN_TIER
        if s:
            c["stats"] = {"source": "SeeMeta", "comp": s["name"], "avg": s["avg"], "top4": s["top4"], "win": s["win"]}
        was = c.get("tier")
        for key in ("tier", "data_tier"):  # data_tier: what it plays at once you have its emblem / augment
            if c.get(key) in _TIERS and _TIERS.index(c[key]) < _TIERS.index(cap):
                c[key] = cap
        if c["tier"] != was:
            why = (f"ranked: top 4 {s['top4']:.0f}%, avg {s['avg']:.2f} ({s['name']})" if s else
                   "no ranked results for it")
            c["notes"] = (f"{c['guide']} {c['guide_tier']} → {cap}, {why}. " + c.get("notes", "")).strip()
    # the same build from two guides shows once: the one rated higher after your caps above (a tie keeps the
    # guide loaded first: bunnymuffins, then OTHER_GUIDES in order). The others' letters go in its notes
    rank = lambda c: _TIERS.index(c["tier"]) if c.get("tier") in _TIERS else len(_TIERS)  # noqa: E731
    kept = []
    for c in comps:
        same = [k for k in kept if k["guide"] not in (c["guide"], "mine") and c["guide"] != "mine"
                and _same_build(c, k)]
        if not same:
            kept.append(c)
            continue
        k = max(same, key=lambda k: _overlap(c, k))
        keep, drop = (c, k) if rank(c) < rank(k) else (k, c)
        if keep is c:
            kept[next(i for i, x in enumerate(kept) if x is k)] = c
        keep["_others"] = keep.get("_others", []) + [drop] + drop.get("_others", [])
        if not keep.get("early") and drop.get("early"):  # TFT Academy's stage 2 opener for the build kept
            keep["early"] = drop["early"]
        if not keep.get("howto") and drop.get("howto"):
            keep["howto"] = drop["howto"]
        if keep.get("board") and not any(b.get("prop") for b in keep["board"]):
            # Elderwood's tree / plant: where the other guide puts them, on the hexes this board leaves free
            taken = {b["pos"] for b in keep["board"]}
            props = [dict(b) for b in drop.get("board") or [] if b.get("prop") and b["pos"] not in taken]
            keep["board"] = sorted(keep["board"] + props, key=lambda b: b["pos"])
    for c in kept:
        said = [f"{o['guide']}: {o.get('guide_tier') or o['tier']} tier ({o['guide_name'].replace(' — ' + o['guide'], '')})"
                for o in c.pop("_others", [])]
        if said:
            c["notes"] = ("; ".join(said) + ". " + c.get("notes", "")).strip()
    comps = kept
    # what a build needs that the shop can't give you: a specific emblem, augment or artifact makes it
    # situational (matcher.strength) until you have it
    artifacts = {n.lower() for n in load_special() if not n.endswith(" Emblem")}
    traits = list(load_set()["traits"])
    items = json.loads((DATA / "items.json").read_text(encoding="utf8"))
    recipe = {v["name"]: v.get("components") or [] for v in items.values()}
    augs = load_augment_names()
    for c in comps:
        texts = [t.split("/")[0].strip() for v in (c.get("carries") or {}).values() for t in v]
        if c.get("special_items_optional"):  # the guide says its emblem / artifact is a bonus
            texts = []
        needs = c.get("needs", "")
        c["forge"] = any(t.lower() in artifacts for t in texts) or \
            "artifact" in (needs + " " + c.get("notes", "")).lower()
        emblems = {t[:-len(" Emblem")] for t in texts if t.endswith(" Emblem")}
        m = re.search(r"— (.+?) Emblem$", c["name"])
        if m:
            emblems.add(m.group(1))
        emblems |= {t for t in traits if re.search(rf"\b{re.escape(t)} emblem", needs, re.I)}
        # how each emblem can be made: a Spatula / Frying Pan recipe, else only from augments etc.
        c["req_emblems"] = {e: next(("spatula" if "Spatula" in x else "pan" for x in recipe.get(f"{e} Emblem", [])
                                     if "Spatula" in x or "FryingPan" in x), None) for e in sorted(emblems)}
        c["req_augs"] = [a for a in augs if a.lower() in needs.lower()]
        c["must"] = must_items(c, (over.get(c["name"].split(" — ")[0].strip()) or {}).get("must"))
    return comps


# the guide author's short item names -> words in the real names
_ITEM_ABBR = {"ie": "infinity edge", "jg": "jeweled gauntlet", "hoj": "hand of justice", "qss": "quicksilver",
              "tg": "thief gloves", "red": "red buff", "blue": "blue buff", "eon": "edge of night",
              "lw": "last whisper", "gs": "giant slayer", "bt": "bloodthirster", "morello": "morellonomicon"}


def _item_norm(s):
    s = s.lower().replace("’", "'").replace("*", "").strip()
    return re.sub(r"'s\b|'", "", s)


def must_items(comp, extra=None):
    """{carry: [its items the guide marks as must-have]} — bunnymuffins writes them in bold ("Gunblade",
    "Crownguard, Gargoyle's", "Shojin/Blue": either one), matched to the item names in the build. `extra`:
    your own ({"Azir": ["Guinsoo's"]}, comp_overrides.json "must")."""
    out = {}
    lines = [(unit, rest[0] if rest else []) for unit, _text, *rest in (comp.get("howto") or {}).get("items") or []]
    for unit, tokens in lines + list((extra or {}).items()):
        have = (comp.get("carries") or {}).get(unit) or []
        for token in tokens:
            for alt in token.split("/"):
                t = _item_norm(alt)
                t = _ITEM_ABBR.get(t, t)
                for word in (t, t.rstrip("s")):  # "Warmogs" -> "Warmog's Armor"
                    hit = next((i for i in have if re.search(rf"\b{re.escape(word)}", _item_norm(i))), None)
                    if hit:
                        out.setdefault(unit, [])
                        if hit not in out[unit]:
                            out[unit].append(hit)
                        break
    return out
