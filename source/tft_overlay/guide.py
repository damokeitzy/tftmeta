"""Static build guide: everything comes from comps.json + set data, nothing from the screen.

Riot's policy allows "a static recommendation for a player pre-game ... even if the same advice
is available throughout the game", but not suggestions based on the current game state. So a
guide is a pure function of the chosen comp — it never changes with what happens in the match.
"""
from collections import Counter

from .matcher import comp_traits
from .slams import wants_for


def component_priority(comp, book):
    """[(component api, count)] across all BIS items, main carry first — what to grab from
    carousels and item drops for this build."""
    counts, order = Counter(), {}
    for _, options in wants_for(comp, book):
        for c in book.recipe(options[0]):
            counts[c] += 1
            order.setdefault(c, len(order))
    return sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def key_traits(comp, set_data):
    """[(trait, units in comp, active breakpoint)] for traits the final board activates."""
    out = []
    for trait, n in comp_traits(comp, set_data["champions"]).most_common():
        reached = [b for b in set_data["traits"].get(trait, []) if b <= n]
        if reached:
            out.append((trait, n, max(reached)))
    return out


def board_order(comp, set_data):
    """Units in the order you'd actually field them: carries first (priority order), then the
    units that add the most to the comp's traits, cheapest first. The first 7 are a realistic
    level 7 board; the rest are level 8–9 upside."""
    champs = set_data["champions"]
    profile = comp_traits(comp, champs)
    carries = [u for u in comp["carries"] if u in comp["units"]]
    rest = [u for u in comp["units"] if u not in carries and u in champs]
    rest.sort(key=lambda u: (-sum(profile[t] for t in champs[u]["traits"] if profile[t] >= 2),
                             champs[u]["cost"]))
    return carries + rest


def pivots(comp, comps, book, n=2):
    """Comps that are easiest to switch into: most shared units, then shared BIS items.
    Uses the comp's own "pivots" list from comps.json when it has one."""
    if comp.get("pivots"):
        return comp["pivots"][:n]
    mine = {api for _, options in wants_for(comp, book) for api in options[:1]}
    scored = []
    for other in comps:
        if other["name"] == comp["name"]:
            continue
        shared_units = len(set(comp["units"]) & set(other["units"]))
        theirs = {api for _, options in wants_for(other, book) for api in options[:1]}
        scored.append((shared_units + 0.5 * len(mine & theirs), other["name"]))
    scored.sort(reverse=True)
    return [name for score, name in scored[:n] if score >= 2]


def build_guide(comp, book, set_data, comps=()):
    champs = set_data["champions"]
    carries = []
    for unit, item_texts in comp["carries"].items():
        items = []
        for text in item_texts:
            options = book.resolve(text)
            if options:  # icon of the first option, label keeps alternatives ("Shojin / Blue Buff")
                items.append((book.items[options[0]]["icon"], text))
            else:
                items.append((None, text))  # "Leftover AP", emblems… shown as text
        carries.append((unit, champs.get(unit, {}).get("icon"), items))
    return {
        "name": comp["name"],
        "tier": comp["tier"],
        "units": [(u, champs[u]["icon"], champs[u]["cost"], u in comp["carries"])
                  for u in board_order(comp, set_data)],
        "carries": carries,
        "components": [(book.items[c]["icon"], book.short(c), n) for c, n in component_priority(comp, book)],
        "traits": key_traits(comp, set_data),
        "augments": comp.get("augments", []),
        "notes": comp.get("notes", ""),
        "plan": comp.get("plan"),
        "pivots": pivots(comp, comps, book) if comps else [],
    }
