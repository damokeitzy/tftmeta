"""Rank the augments on the pick screen for your comp.

teamfight.lol lists a few best augments per comp; anything else is judged from the augment's own
description against the comp: does it name one of the comp's traits or units, hand out units of
the comp's reroll cost, give items/artifacts/emblems (and does the comp need one), or gold/XP.
Simple keyword rules — a nudge, not a solver.
"""
import json
import re

from .gamedata import DATA
from .matcher import comp_traits

_DESCS = None


def descriptions():
    global _DESCS
    if _DESCS is None:
        path = DATA / "augments.json"
        _DESCS = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    return _DESCS


def reroll_cost(comp, champions):
    """Cost of the main carry if the comp is a reroll comp, else None."""
    if "Reroll" not in comp["name"] and "reroll" not in (comp.get("plan") or {}).get("style", "").lower():
        return None
    main = next(iter(comp.get("carries") or {}), None)
    return champions.get(main, {}).get("cost")


def rank_augments(names, comp, champions):
    """-> [(name, score, reason)] best first."""
    listed = " ".join(comp.get("augments", [])).lower()
    traits = [t for t, n in comp_traits(comp, champions).items() if n >= 2]
    rc = reroll_cost(comp, champions)
    needs = (comp.get("needs") or "").lower()
    out = []
    for name in names:
        d = descriptions().get(name, "")
        text = f"{name} {d}"
        low = text.lower()
        if name.lower() in listed:
            out.append((name, 90, "teamfight.lol pick for this comp"))
            continue
        hit_trait = next((t for t in traits if t.lower() in low), None)
        hit_unit = next((u for u in comp["units"] if re.search(rf"\b{re.escape(u)}\b", text)), None)
        if hit_trait:
            out.append((name, 75, f"+{hit_trait} (your trait)"))
        elif hit_unit:
            out.append((name, 70, f"helps {hit_unit}"))
        elif ("emblem" in low and "emblem" in needs) or ("artifact" in low and "artifact" in needs):
            out.append((name, 70, "gives what this comp needs"))
        elif rc and re.search(rf"\b{rc}-cost\b", low) and "champion" in low:
            out.append((name, 65, f"{rc}-cost units for your reroll"))
        elif rc and ("duplicator" in low or "copy" in low):
            out.append((name, 62, "copies units for your reroll"))
        elif any(k in low for k in ("artifact", "anvil", "component", "emblem", " item")):
            out.append((name, 55, "items"))
        elif any(k in low for k in (" gold", "interest", " xp")):
            out.append((name, 50 if not rc else 55, "econ" + (" (good for rolling)" if rc else "")))
        elif any(k in low for k in ("attack speed", "health", "damage", "ability power", "armor")):
            out.append((name, 45, "combat stats"))
        else:
            out.append((name, 30, "no clear fit"))
    out.sort(key=lambda r: -r[1])
    return out
