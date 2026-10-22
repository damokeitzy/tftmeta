"""Checks the logged shops (data/shop_log/*.jsonl) against the shop's own posted odds.

  py tools/shop_report.py

Uses the posted odds read off the screen (falling back to data/shop_odds.json, the Set 18 table)
and the Set 18 pool sizes. For each odds bracket (your level), compares the share of each cost you actually saw with the
odds shown above the shop, and says whether the gap is bigger than luck explains. Also checks
whether 4-costs you'd already bought show up more than an even share (the pool should make
them *rarer*), and how often the same unit repeats in back-to-back shops.

"Bigger than luck" uses a z-score: |z| >= 3 means a gap that pure chance produces well under
1% of the time. Small samples can't show much — expect a few hundred slots per bracket before
the numbers mean anything.
"""
import os
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("TFTMETA_ROOT") or Path(__file__).resolve().parent.parent)


TABLE = json.loads((ROOT / "data" / "shop_odds.json").read_text(encoding="utf8"))
POOL = {int(k): v for k, v in TABLE["pool"].items()}  # copies of each champion, by cost


def dedupe(rows):
    """Older logs recorded the same shop several times (hover/buy redraws). Keep only real new
    shops, using the same rule the logger now uses."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tft_overlay.shoplog import is_new_shop
    out, last = [], None
    for r in rows:
        if last is None or is_new_shop(last, r["shop"]):
            out.append(r)
            last = list(r["shop"])
        else:  # same shop: track what's left in it
            last = [u if r["shop"][i] else None for i, u in enumerate(last)]
    return out


def load():
    rows = []
    for f in sorted((ROOT / "data" / "shop_log").glob("*.jsonl")):
        for line in f.read_text(encoding="utf8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("level") and not 1 <= r["level"] <= 10:  # misread ("Lvl.11")
                continue
            if r.get("level") and not r.get("odds"):  # odds unreadable: use the Set 18 table
                r["odds"] = TABLE["levels"].get(str(r["level"]))
            if r.get("odds") and r.get("level"):
                rows.append(r)
    return dedupe(rows)


def z(observed, n, p):
    if n == 0 or p <= 0 or p >= 1:
        return 0.0
    return (observed - n * p) / math.sqrt(n * p * (1 - p))


def verdict(zv):
    a = abs(zv)
    if a >= 3:
        return "NOT luck" + (" (too many)" if zv > 0 else " (too few)")
    if a >= 2:
        return "unusual, watch it"
    return "within luck"


def main():
    rows = load()
    champs = json.loads((ROOT / "data" / "set.json").read_text(encoding="utf8"))["champions"]
    n_cost = Counter(c["cost"] for c in champs.values())
    if not rows:
        print("No shops logged yet — play a few games with the overlay running.")
        return
    print(f"{len(rows)} shops logged ({sum(sum(1 for u in r['shop'] if u) for r in rows)} slots)\n")

    print("1) COST SHARE vs POSTED ODDS, per odds bracket")
    by_odds = defaultdict(list)
    for r in rows:
        by_odds[(r["level"], tuple(r["odds"]))].append(r)
    for (level, odds), group in sorted(by_odds.items()):
        costs = Counter(c for r in group for c in r["costs"] if c)
        n = sum(costs.values())
        table = TABLE["levels"].get(str(level))
        note = "" if not table or list(odds) == table else f"  (Set 18 table says {'/'.join(map(str, table))} — patch change or misread)"
        print(f"   Level {level}  posted {'/'.join(f'{o}%' for o in odds)}  — {len(group)} shops, {n} slots{note}")
        for cost in range(1, 6):
            p = odds[cost - 1] / 100
            if p == 0 and costs[cost] == 0:
                continue
            obs = costs[cost]
            zv = z(obs, n, p)
            print(f"      {cost}-cost: saw {obs:4d} ({100 * obs / n:5.1f}%)  expected {n * p:6.1f} "
                  f"({100 * p:4.1f}%)  z={zv:+.1f}  {verdict(zv)}")
    # the user's claim: more 4-costs at the lower-odds bracket than the higher one
    four = [(odds[3], sum(1 for r in g for c in r["costs"] if c == 4), sum(1 for r in g for c in r["costs"] if c))
            for (_, odds), g in by_odds.items() if odds[3] > 0]
    if len(four) >= 2:
        print("\n   4-cost share by posted 4-cost odds (higher odds should give a higher share):")
        for p, k, n in sorted(four):
            print(f"      posted {p:2d}%  ->  saw {100 * k / max(n, 1):5.1f}%  ({k}/{n} slots)")

    print("\n2) 4-COSTS YOU'D ALREADY BOUGHT")
    hits = total = 0
    expected = 0.0
    for r in rows:
        owned = r.get("owned", {})
        owned4 = {u: n for u, n in owned.items() if champs.get(u, {}).get("cost") == 4}
        left_all = POOL[4] * n_cost[4] - sum(min(n, POOL[4]) for n in owned4.values())
        p_owned = sum(POOL[4] - min(n, POOL[4]) for n in owned4.values()) / max(left_all, 1)
        for u, c in zip(r["shop"], r["costs"]):
            if c != 4:
                continue
            total += 1
            expected += p_owned  # with your bought copies taken out of the pool
            hits += u in owned4
    if total:
        zv = z(hits, total, expected / total) if expected else 0.0
        print(f"   {total} 4-cost slots: {hits} were units you'd bought; the pool (10 copies each, minus "
              f"yours) predicts {expected:.1f}. Other players' copies aren't seen, so the true number "
              f"should be a bit lower still. z={zv:+.1f}  {verdict(zv)}")
    else:
        print("   no 4-cost slots logged yet")

    print("\n3) SAME UNIT IN BACK-TO-BACK SHOPS (within one roll-down)")
    for cost in (3, 4):
        repeats = pairs = 0
        exp = 0.0
        for a, b in zip(rows, rows[1:]):
            if b["t"] - a["t"] > 20:  # not the same roll-down
                continue
            ua = {u for u, c in zip(a["shop"], a["costs"]) if c == cost}
            ub = [u for u, c in zip(b["shop"], b["costs"]) if c == cost]
            if not ua:
                continue
            pairs += 1
            repeats += any(u in ua for u in ub)
            owned = b.get("owned", {})
            left = POOL[cost] * n_cost[cost] - sum(min(n, POOL[cost]) for u2, n in owned.items()
                                                   if champs.get(u2, {}).get("cost") == cost)
            share = sum(POOL[cost] - min(owned.get(u, 0), POOL[cost]) for u in ua) / max(left, 1)
            p_slot = b["odds"][cost - 1] / 100 * share  # pool-aware
            exp += 1 - (1 - p_slot) ** 5
        if pairs:
            zv = z(repeats, pairs, exp / pairs)
            print(f"   {cost}-cost: a unit from one shop reappeared in the next {repeats}/{pairs} times, "
                  f"luck predicts about {exp:.1f}. z={zv:+.1f}  {verdict(zv)}")

    print("\nRead it like this: one bracket with |z| >= 3 is worth a closer look; consistent |z| >= 3 "
          "across many games would mean the shop doesn't match its posted odds.")


if __name__ == "__main__":
    main()
