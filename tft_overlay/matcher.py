"""Scores stored comps against what's on screen."""
from collections import Counter

TIER_WEIGHT = {"S": 1.15, "A": 1.05, "B": 1.0, "C": 0.9, "D": 0.8, "Special": 0.85}


def early(round_):
    """Stage 1-2: the build is still being picked from a few units."""
    return bool(round_) and round_ < "3-1"


def unmet(comp, have=(), round_=None):
    """What this build needs that you don't have: 'forge', 'emblem:X', 'aug:Name', 'guide:<its guide's
    conditions>'. `have` holds tokens for what you have or are offered: 'forge', 'spatula', 'pan',
    'emblem:X', 'aug:Name', 'unit:X', 'items:N', 'gold:50', 'bows:N'. Every stage: a build that needs
    something isn't suggested until you have it (suggest)."""
    have = set(have)
    out = ["forge"] if comp.get("forge") and "forge" not in have else []
    for e, craft in (comp.get("req_emblems") or {}).items():
        if f"emblem:{e}" not in have and not (craft and craft in have):
            out.append(f"emblem:{e}")
    out += [f"aug:{a}" for a in comp.get("req_augs") or [] if f"aug:{a}" not in have]
    req = comp.get("guide_req") or []  # bunnymuffins '*': any one of its conditions will do
    if req and not any(t in have or (t == "emblem:any" and any(h.startswith("emblem:") for h in have))
                       for t in req):
        out.append("guide:" + (comp.get("conditions") or "requirement")[:60])
    return out


def needs_text(comp, miss):
    """unmet() in words: 'an artifact · Invoker Emblem · Consuming Flora (augment)' — and a guide's
    conditions as it writes them ('any of: Artifacts or Emblems, Consuming Flora, …')."""
    out = []
    for m in miss:
        if m == "forge":
            out.append("an artifact")
        elif m.startswith("emblem:"):
            out.append(f"{m[7:]} Emblem")
        elif m.startswith("aug:"):
            out.append(f"{m[4:]} (augment)")
        elif m.startswith("guide:"):
            req = [_req_words(t) for t in comp.get("guide_req") or []]
            out.append(("any of: " + ", ".join(req)) if len(req) > 1 else req[0] if req else m[6:])
    return " · ".join(out)


def _req_words(t):
    """A guide condition token in words: 'items:4' -> '4+ finished items'."""
    kind, _, v = t.partition(":")
    return {"items": f"{v}+ finished items", "gold": f"{v}+ gold", "bows": f"{v} Recurve Bows",
            "unit": f"{v} early", "aug": f"{v} (augment)", "forge": "an artifact",
            "emblem": "an emblem" if v == "any" else f"{v} Emblem"}.get(kind, t)


def strength(comp, have=(), round_=None):
    """How good a comp is, independent of your board: the guide's tier letter (already capped by real
    ranked results in gamedata), lowered while it needs something the shop can't give: a specific
    augment, emblem or artifact makes it situational ("Special") until you have it; bunnymuffins'
    '*' conditions ("Kha'Zix by 2-3", "lots of items") cap it at B."""
    w = TIER_WEIGHT.get(comp.get("tier"), 1)
    if comp.get("data_tier") and not unmet(comp, have) and (
            comp.get("forge") or comp.get("req_emblems") or comp.get("req_augs") or comp.get("guide_req")):
        w = TIER_WEIGHT.get(comp["data_tier"], w)  # you have what it needs: its real tier
    miss = unmet(comp, have, round_)
    if any(m == "forge" or m.startswith(("emblem:", "aug:")) for m in miss):
        w = min(w, TIER_WEIGHT["Special"])
    elif miss:
        w = min(w, TIER_WEIGHT["B"])
    return w


def comp_traits(comp, champions):
    """Trait -> number of distinct comp units that have it."""
    c = Counter()
    for unit in comp["units"]:
        for t in champions[unit]["traits"]:
            c[t] += 1
    return c


_holders_cache = {}


def unique_holders(champions):
    """Traits only one champion has (Bounty Seeker -> Draven): seeing one active on your board
    means that exact champion is fielded."""
    key = id(champions)
    if key not in _holders_cache:
        holders = {}
        for name, ch in champions.items():
            for t in ch["traits"]:
                holders.setdefault(t, []).append(name)
        _holders_cache[key] = {t: names[0] for t, names in holders.items() if len(names) == 1}
    return _holders_cache[key]


def score(comp, traits, shop, champions, bought=None, have=(), round_=None):
    profile = comp_traits(comp, champions)
    overlap = sum(min(n, profile.get(t, 0)) for t, n in traits.items())
    on_board = sum(traits.values()) or 1
    trait_fit = overlap / on_board                      # how much of my board fits this comp
    progress = overlap / max(sum(profile.values()), 1)  # how far along the comp I am
    buys = sorted({u for u in shop if u in comp["units"]})
    s = 0.6 * trait_fit + 0.4 * progress + 0.05 * len(buys)
    # A fielded unique-trait champion identifies the comp far better than shared traits do:
    # Draven on board (Bounty Seeker) -> Draven comps, even if the rest looks like Elderwood.
    for t, champ in unique_holders(champions).items():
        if traits.get(t):
            if champ in comp["carries"]:
                s += 0.6
            elif champ in comp["units"]:
                s += 0.25
    # units you already hold (bench included; 3+ copies = a 2★): the comp your board can
    # actually become beats one that only shares traits
    if bought:
        held = [u for u in comp["units"] if bought.get(u)]
        s += 0.35 * sum(1.5 if bought[u] >= 3 else 1 for u in held) / max(len(comp["units"]), 1)
    s *= strength(comp, have, round_)
    shared = sorted((t for t in traits if profile.get(t)), key=lambda t: -profile[t])
    return s, shared, buys


def suggest(comps, traits, shop, champions, bench=(), held=(), book=None, top=3, adapt=None,
            bought=None, round_=None, pool=None, have=()):
    """bench/held: item apis on the bench / on units; book: slams.ItemBook (optional).
    adapt: comp -> comp with item carries moved to units you own (roster.adapt); used for the
    item advice only, kept as result["item_comp"]. bought / pool: purchases and shop counts this
    game (shop logger); with round_, comps whose core units you won't get drop down."""
    from .roster import core_missing
    from .slams import plan

    results = []
    for comp in comps:
        s, shared, buys = score(comp, traits, shop, champions, bought, have, round_)
        missing = core_missing(comp, traits, bought, champions, round_, pool)
        if missing:
            s *= 0.45  # no Kayle by 3-1 for a Kayle reroll: not your comp any more
        # a comp that "needs" an item you're holding (Defender Emblem for Defender Diana) is on
        if book and comp.get("needs") and held:
            flat = [a for u in held for a in (u if isinstance(u, (list, tuple)) else [u])]
            if any(a in book.items and book.name(a).lower() in comp["needs"].lower() for a in flat):
                s += 0.3
        item_comp = adapt(comp) if adapt else comp
        slams, matched = plan(item_comp, bench, held, book) if book else ([], 0)
        s += 0.04 * matched  # finished items that are BIS for this comp pull it up
        needs = unmet(comp, have, round_)
        results.append({"comp": comp, "score": s, "shared": shared, "buys": buys, "slams": slams,
                        "item_comp": item_comp, "missing_core": missing, "needs": needs,
                        "needs_text": needs_text(comp, needs)})
    # a build that needs something you don't have (a specific augment / emblem / artifact, or its guide's
    # conditions) is never suggested: it goes after every build you can play, until you have it
    if not traits and not held:  # nothing read yet: just show the best tiers
        results.sort(key=lambda r: (bool(r["needs"]), -strength(r["comp"], have, round_)))
    else:
        # highest tier first, among the builds your board can actually go into (at least 2/3 as good
        # a fit as the best one; half in stage 1-2, when 2-3 cheap units say little and reroll builds
        # out-fit everything); fit only decides within a tier — an A build beats a B that fits a bit
        # better. strength() already applies the caps (needs an emblem / augment / forge item).
        best = max((r["score"] for r in results), default=0) or 1

        def bar(r):  # stage 1-2: half the best fit, or 40% for a build that uses a unit you've bought
            if not early(round_):
                return best * 0.65
            return best * (0.4 if bought and any(bought.get(u) for u in r["comp"]["units"]) else 0.5)
        results.sort(key=lambda r: (2, -r["score"], 0) if r["needs"]
                     else (0, -strength(r["comp"], have, round_), -r["score"]) if r["score"] >= bar(r)
                     else (1, -r["score"], 0))  # the rest: closest fit first
    for r in results:  # the tier it plays at right now (situational while it needs an emblem / augment / forge)
        w = strength(r["comp"], have, round_)
        r["tier_now"] = next((t for t, v in TIER_WEIGHT.items() if v == w), r["comp"].get("tier"))
    return results[:top]
