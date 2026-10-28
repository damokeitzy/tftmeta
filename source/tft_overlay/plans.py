"""Game plan + leveling timeline per comp style (reroll 1/2/3-cost, level 8, fast 9).

These are standard TFT leveling patterns, not comp-specific data — teamfight.lol's builder
doesn't publish leveling. Override any comp's `plan` in data/comps.json.
"""
import re

LEVELING = {
    "reroll1": [("2-1", "Lv 4 · stay"), ("3-1", "Lv 4 · roll for 3★"), ("3★ carries", "Lv 6–7"),
                ("4-5 / 5-1", "Lv 8")],
    "reroll2": [("2-1", "Lv 4"), ("2-5", "Lv 5"), ("3-1", "Lv 6 · slow roll above 50g"),
                ("4-1", "Lv 7"), ("4-5", "Lv 8")],
    "reroll3": [("2-1", "Lv 4"), ("2-5", "Lv 5"), ("3-2", "Lv 6"),
                ("3-5 / 4-1", "Lv 7 · slow roll above 50g"), ("5-1", "Lv 8")],
    "standard": [("2-1", "Lv 4"), ("2-5", "Lv 5"), ("3-2", "Lv 6"), ("3-5", "Lv 7"),
                 ("4-1 / 4-2", "Lv 8 · roll down"), ("5-1", "Lv 9 if healthy")],
    "fast9": [("2-1", "Lv 4"), ("2-5", "Lv 5"), ("3-2", "Lv 6"), ("3-5", "Lv 7"), ("4-1", "Lv 8"),
              ("4-5 / 5-1", "Lv 9 · roll for legendaries")],
}

STYLES = {
    "reroll1": ("Reroll (1-cost carry)", "Level 4",
                "Stay level 4: 55% of shop slots are 1-costs there (45% at 5, 30% at 6 — every level floods the shop with 2- and 3-costs). Roll at 3-1 until the main carry is 3★, then level up for the rest of the board. No 3★ by 4-1: level and play for 2★s."),
    "reroll2": ("Reroll (2-cost carry)", "Level 6",
                "Slow roll at level 6 from 3-1 / 3-2, rolling only gold above 50. Once the carries are 3★, level to 7–8."),
    "reroll3": ("Reroll (3-cost carry)", "Level 7",
                "Level to 7 around 3-5 / 4-1, then slow roll the gold above 50. If you have to roll early to stabilise, roll at 6 for the 2-cost pieces only — then rebuild gold and get to 7 before rolling for 3-costs (3-cost odds at 6 are only 25%)."),
    "fast9": ("Fast 9", "Level 9",
              "Play strongest board and stay healthy; level 8 at 4-1, 9 around 4-5 / 5-1, then roll for legendaries. Don't roll below 50 gold before 9 unless you're about to die."),
    "standard": ("Level 8 / 4-cost carries", "Level 8",
                 "Econ to 50 and level 8 around 4-1 / 4-2, then roll down for the 4-cost carries. Upgrade to 5-costs at 9 if you're healthy."),
}

REALISTIC = {
    "reroll": "Realistic target: main carry 3★, the rest 2★, at level 7 with full items. If the carry is still 2★ by 4-5, stop rolling — level and play for top 4. A 3★ board is the upside, not the plan.",
    "fast9": "Realistic target: a level 8 board of 2★ 4-costs with the carry's items. Level 9 and legendaries only if you're healthy — most games end at 8.",
    "standard": "Realistic target: 2★ 4-cost carries with full items at level 8. 5-costs and 3★ 4-costs are upside.",
}


# Tocker's Trials (PvE): 30 rounds from 1-1 to 3-10, three lives, no streaks; income is interest (max 5) plus
# each round's reward. Passive XP alone gets you Lv 5 by 2-1 and Lv 6 by 3-1 (you can't stay at 4), the rest
# is bought. Nobody else takes copies, so 3★s cost far less than in a lobby, and Riot's score counts
# surviving champions, fight time, star-ups and unused items. Timelines follow your own Tocker's runs
# (Lv 7 around 2-6 to 2-8, Lv 8 around 3-6).
TOCKERS_LEVELING = {
    "reroll1": [("2-1", "Lv 5 · roll above 50 for 3★"), ("3-1", "Lv 6"), ("3-3", "Lv 7"), ("3-6", "Lv 8")],
    "reroll2": [("2-1", "Lv 5"), ("2-3", "Lv 6 · roll above 50 for 3★"), ("2-9", "Lv 7"), ("3-6", "Lv 8")],
    "reroll3": [("2-1", "Lv 5"), ("2-3", "Lv 6"), ("2-6", "Lv 7 · roll above 50 for 3★"), ("3-6", "Lv 8")],
    "standard": [("2-1", "Lv 5"), ("2-3", "Lv 6"), ("2-6", "Lv 7"), ("2-10", "Lv 8 · roll down"), ("3-5", "Lv 9")],
    "fast9": [("2-1", "Lv 5"), ("2-3", "Lv 6"), ("2-6", "Lv 7"), ("2-10", "Lv 8"),
              ("3-3", "Lv 9 · roll for 5-costs")],
}
TOCKERS_ROLL_AT = {"reroll1": "Level 5", "reroll2": "Level 6", "reroll3": "Level 7", "standard": "Level 8",
                   "fast9": "Level 9"}


def tockers_plan(plan):
    """The same build's plan for Tocker's Trials: its roll level and leveling timeline in Tocker's rounds."""
    style = (plan or {}).get("style", "")
    m = re.search(r"(\d)-cost", style)
    key = (f"reroll{m.group(1)}" if "Reroll" in style and m and m.group(1) in "123" else
           "fast9" if "Fast 9" in style else "standard")
    roll = TOCKERS_ROLL_AT[key]
    when = (f"Tocker's: roll the gold above 50 at {roll.lower()} until the carries are 3★ — nobody else takes "
            f"copies, so they come cheap and every star-up scores. Then level, and 3★ the board's other cheap units."
            if key.startswith("reroll") else
            "Tocker's: level on the timeline, roll down at 8 around 2-10 for 2★ 4-costs, then 3★ them at 9 — "
            "the pool is all yours.")
    return dict(plan or {}, roll_at=roll, when=when, leveling=TOCKERS_LEVELING[key],
                bail="A lost round costs one of your three lives: after a loss, roll for upgrades before the next fight.",
                realistic="Score (game files): Surviving Champs, Time Bonus, Star Levels, Gold Interest, Item Greed, "
                          "Close Calls. A board that wins quickly with everyone alive scores more than one that "
                          "scrapes through. Keep 50 gold for interest until the last round, 3-10.")


def style_key(name, main_carry_cost):
    if "Fast 9" in name:
        return "fast9"
    if "Reroll" in name and main_carry_cost <= 3:
        return f"reroll{main_carry_cost}"
    return "standard"


def make_plan(name, main_carry_cost):
    key = style_key(name, main_carry_cost)
    style, roll_at, when = STYLES[key]
    family = "reroll" if key.startswith("reroll") else key
    bail = ("Switch if 2+ opponents hold your carry, or you're under ~30 HP at 4-1 without a 2★ carry."
            if family == "reroll" else
            "Switch if you can't find the carries by your roll-down, or the lobby is heavily contested.")
    return {"style": style, "roll_at": roll_at, "when": when, "bail": bail,
            "realistic": REALISTIC[family], "leveling": LEVELING[key]}
