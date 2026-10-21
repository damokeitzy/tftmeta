"""Which of the comp's carries you actually have, and who holds their items when you don't.

Items can't go on an Ornn you don't own. A unit is not on your board when one of its traits is
missing from the trait panel, and the shop logger knows what you bought this game. A carry that is
neither bought nor on the board is "missing": its items go to a unit you do have that plays the
same spot (frontline for frontline), or wait at the back of the queue. The main carry is never
swapped out: you hold its items while you roll for it.

Core units have a deadline: a 2-cost reroll with no Kayle (or no Ornn) by 2-5 isn't happening, so
the comp drops and you build around what you hold. 2-5 is before the 3-2 augment and before items
are locked in; later than that the pivot costs too much. Earlier than that, a core unit you don't have
that also shows up far less than the odds say (0 Ornn in 36 one-cost slots) is being held by
someone else: same trigger.
"""
import math
import re

# core unit's cost -> the stage by which you should hold at least one copy. Reroll comps decide at
# 2-5 whatever the cost (no Kha'Zix by 2-5 = not your comp): later, the 3-2 augment and your items
# are committed. Level comps get until their carries' shop odds are real.
DEADLINE = {1: (2, 5), 2: (2, 5), 3: (3, 2), 4: (4, 1)}
REROLL_DEADLINE = (2, 5)
RARE_P = 0.1      # seeing this few copies by chance is under 10%...
RARE_SLOTS = 15   # ...over at least this many shop slots of its cost


def _stage(round_):
    m = re.match(r"(\d)-(\d)", round_ or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_reroll(comp):
    return "reroll" in comp["name"].lower() or "reroll" in (comp.get("plan") or {}).get("style", "").lower()


def core_units(comp, champions):
    """The units the comp can't do without: the main carry, and in reroll comps every cheap
    (1-2 cost) carry too (Ornn in Kayle Reroll)."""
    carries = [u for u in comp.get("carries", {}) if u in champions]
    if not carries:
        return []
    core = carries[:1]
    if is_reroll(comp):
        core += [u for u in carries[1:] if champions[u]["cost"] <= 2]
    return core


def rarity(unit, pool, champions):
    """-> (seen, slots, expected, P(seeing this few by chance)) from this game's shops, or None.
    pool = (seen Counter, slots-by-cost Counter) from the shop logger."""
    if not pool or unit not in champions:
        return None
    seen, slots = pool
    cost = champions[unit]["cost"]
    n = slots.get(cost, 0)
    kinds = sum(1 for c in champions.values() if c["cost"] == cost)
    if not n or not kinds:
        return None
    p, k = 1 / kinds, seen.get(unit, 0)
    prob = sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    return k, n, n * p, prob


def rare(unit, pool, champions):
    r = rarity(unit, pool, champions)
    return bool(r) and r[1] >= RARE_SLOTS and r[3] < RARE_P


def core_missing(comp, traits, bought, champions, round_, pool=None):
    """Core units you don't have and won't get: past their deadline, or already rare in your
    shops (someone else holds them). Needs the shop logger (bought=None: unknown, never
    triggers) and a read trait panel."""
    st = _stage(round_)
    if bought is None or not traits or not st:
        return []
    out = []
    for u in core_units(comp, champions):
        if have(u, traits, bought, champions):
            continue
        dl = REROLL_DEADLINE if is_reroll(comp) else DEADLINE.get(champions[u]["cost"])
        if (dl and st >= dl) or (st >= (2, 3) and rare(u, pool, champions)):
            out.append(u)
    return out


def why_missing(units, round_, pool, champions):
    """'no Ornn by 3-3 (Ornn: 0 seen, ~2.6 expected, someone holds them)'"""
    bits = []
    for u in units:
        r = rarity(u, pool, champions)
        if r and r[1] >= RARE_SLOTS and r[3] < RARE_P:
            bits.append(f"{u}: {r[0]} seen, ~{r[2]:.1f} expected, someone holds them")
    head = f"no {' / '.join(units)} by {round_}"
    return head + (f" ({'; '.join(bits)})" if bits else "")


def on_board(unit, traits, champions):
    """Could this unit be on your board? Any one of its traits shows in the trait panel.
    Deliberately lenient: inactive traits ("Lunar 1/2") are greyed out and the OCR can miss them,
    units from orbs/carousel never show as purchases, and telling you to pivot off a carry you're
    holding is worse than missing a pivot."""
    ch = champions.get(unit)
    return bool(ch) and any(traits.get(t, 0) >= 1 for t in ch["traits"])


def have(unit, traits, bought, champions):
    return bool((bought or {}).get(unit)) or on_board(unit, traits, champions)


def early_ok(comp, champions, round_):
    """Can this comp field a strong board before stage 4? Its main carry costs 3 or less (or it's
    stage 4 already). A 4-cost carry comp needs an early board you don't have yet."""
    st = _stage(round_)
    main = next(iter(comp.get("carries") or {}), None)
    return not st or st >= (4, 1) or champions.get(main, {}).get("cost", 5) <= 3


def missing_carries(comp, traits, bought, champions):
    """Carries you almost certainly don't have. Nothing is inferred before the traits are read."""
    if not traits:
        return []
    return [u for u in comp.get("carries", {}) if u in champions and not have(u, traits, bought, champions)]


def _melee(unit, champions):
    return (champions[unit].get("range") or 1) <= 1.5


def stand_in(carry, comp, traits, bought, champions, taken=()):
    """A unit you have that can wear this carry's items: same spot (melee/ranged), not already a
    carry. Bought units beat ones only guessed from traits; then cost, then HP."""
    carries = set(comp.get("carries", {}))
    bought = bought or {}
    pool = list(dict.fromkeys(list(comp.get("units", [])) + [u for u, n in bought.items() if n]))
    cands = [u for u in pool if u in champions and u not in carries and u not in taken
             and have(u, traits, bought, champions) and _melee(u, champions) == _melee(carry, champions)]
    if not cands:
        return None
    return max(cands, key=lambda u: (bool(bought.get(u)), u in comp.get("units", []),
                                     champions[u]["cost"], champions[u].get("hp") or 0))


def adapt(comp, traits, bought, champions):
    """-> (comp with carries re-keyed to units you have, {shown unit: label}).

    Ornn missing, Sejuani on board: Ornn's items become Sejuani's ("Sejuani (Ornn's items)").
    No stand-in: that carry's items go last. The main carry keeps its items ("not found yet")."""
    missing = missing_carries(comp, traits, bought, champions)
    if not missing:
        return comp, {}
    main = next(iter(comp["carries"]))
    carries, tail, labels, taken, wears = {}, {}, {}, set(), {}
    for unit, items in comp["carries"].items():
        if unit not in missing:
            carries[unit] = items
        elif unit == main:
            carries[unit] = items
            labels[unit] = f"{unit} · not found yet"
        else:
            sub = stand_in(unit, comp, traits, bought, champions, taken)
            if sub:
                taken.add(sub)
                wears[sub] = unit
                carries[sub] = items
                labels[sub] = f"{sub} ({unit}'s items)"
            else:
                tail[unit] = items
                labels[unit] = f"{unit} · not owned"
    carries.update(tail)
    return dict(comp, carries=carries, _missing=list(tail), _for=wears,
                _main_missing=main in missing), labels
