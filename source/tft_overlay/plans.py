"""Game plan + leveling timeline per comp style (reroll 1/2/3-cost, level 8, fast 9).

These are standard TFT leveling patterns, not comp-specific data — teamfight.lol's builder
doesn't publish leveling. Override any comp's `plan` in data/comps.json.
"""

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
