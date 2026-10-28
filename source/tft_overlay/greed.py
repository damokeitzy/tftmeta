"""Tocker's Trials Item Greed: every item left on your bench (a component counts too) scores 2,500 at the end of
each round. Your 800k run scored about 36k a round with its items on the bench (1-2 to 2-5) and about 21k after
equipping them. So equip only what the board needs and keep the rest.

What the board needs, by round (from your captured runs): nothing until 2-5, whose enemy is too hard without
items. Then the main tank's and the main carry's first guide item. From 3-1 two each, since every enemy is 2★
with 3 items by then. From 3-6 their full builds (10 Dravens, the wolf pack, the dragon boss at 3-10)."""
import re
from collections import Counter

PER_ITEM = 2500
BUDGET = (((3, 6), 3), ((3, 1), 2), ((2, 5), 1))  # from this round on: items each for the tank and the carry
ROUNDS = [(s, r) for s in (1, 2, 3) for r in range(1, 11)]  # Tocker's: 1-1 to 3-10


def _key(round_):
    m = re.match(r"(\d)-(\d+)", round_ or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def budget(round_):
    k = _key(round_)
    return next((n for start, n in BUDGET if k >= start), 0)


def rounds_left(round_):
    """Rounds still to score, this one included (1-9 -> 22: 1-9 to 3-10)."""
    k = _key(round_)
    return sum(r >= k for r in ROUNDS)


def advise(comp, slots, bench, round_, tank, book):
    """-> {"keep": bench items to leave there, "equip": [(unit, item, bench pieces it uses)], "per_round": what the
    kept items score each round, "by_end": that until 3-10, "from": the round equipping starts, or None once it has}.
    slots: fill()'s {unit: [(status, guide item, ...)]}; tank: the main tank (None if there isn't one)."""
    n, equip, pool = budget(round_), [], Counter(bench)
    if n:
        missing = set(comp.get("_missing") or ())
        carry = next((u for u in comp["carries"] if u != tank and u not in missing), None)
        for unit in [u for u in (tank, carry) if u and u not in missing]:
            for status, item, *_ in slots.get(unit, [])[:n]:
                if status in ("done", "covered", "full"):
                    continue  # already on the champion
                parts = [item] if pool[item] else book.recipe(item)
                if parts and not Counter(parts) - pool:  # on the bench now, built or as its two components
                    pool -= Counter(parts)
                    equip.append((unit, item, parts))
    keep = list(pool.elements())
    per = len(keep) * PER_ITEM
    return {"keep": keep, "equip": equip, "per_round": per, "by_end": per * rounds_left(round_),
            "from": None if n else "2-5"}
