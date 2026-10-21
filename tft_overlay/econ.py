"""Level / roll advice from what's on screen: round, level + XP, gold, win/loss streak, HP,
and the comp's game plan (comps.json `plan`: style, roll level, leveling timeline).

Standard TFT economy rules, kept simple and explicit:
  - interest is 1 gold per 10 held, up to 5 at 50 — don't drop below a breakpoint for nothing;
  - Buy XP = 4 XP for 4 gold;
  - 45 HP or less from 3-1, or a round that cost 15+ HP -> stabilise (roll) before anything else;
  - loss streak early with healthy HP -> keep it (bank gold, don't roll);
  - win streak -> spend on levels to keep the board ahead;
  - reroll comps: at the roll level, roll only the gold above 50;
  - level/fast 9 comps: follow the leveling timeline, roll down at the plan's roll level.
"""
import math
import re

from .hud import shift
from .reader import PLAYERS as PLAYER_LIST, crop

LVL = (0.178, 0.815, 0.240, 0.850)     # "Lvl.6"
XP = (0.240, 0.815, 0.285, 0.850)      # "16/36" (read apart: one rec pass merges them into "616/36")
GOLD = (0.515, 0.815, 0.565, 0.850)
STREAK_NUM = (0.592, 0.815, 0.615, 0.850)
STREAK_ICON = (0.575, 0.815, 0.592, 0.850)
# reroll carry cost -> the round the 3★ chase stops: no 3★ by then, sell the spare copies and level
CHASE_UNTIL = {1: (4, 1), 2: (4, 2), 3: (5, 5)}  # 3-costs: Azir/Rammus-style builds slow roll into stage 5


def read_econ(reader, frame):
    """-> {"level", "xp", "xp_need", "gold", "streak" (+win / -loss), "hp"}; missing -> None."""
    out = {"level": None, "xp": None, "xp_need": None, "gold": None, "streak": None, "hp": None}
    m = re.search(r"Lv\w*\.?\s*(\d+)", reader.text(crop(frame, shift(LVL)), "econ_lvl"))
    if m and 1 <= int(m.group(1)) <= 10:
        out["level"] = int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*(\d+)", reader.text(crop(frame, shift(XP)), "econ_xp"))
    if m:
        out["xp"], out["xp_need"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)", reader.text(crop(frame, shift(GOLD)), "econ_gold"))
    if m:
        out["gold"] = int(m.group(1))
    m = re.findall(r"\d+", reader.text(crop(frame, shift(STREAK_NUM)), "econ_streak"))
    if m:
        n = int(m[-1])
        icon = crop(frame, shift(STREAK_ICON)).reshape(-1, 3).astype(int)
        bright = icon[icon.max(axis=1) > 110]  # the flame itself
        if len(bright):
            r, _, b = bright.mean(axis=0)
            out["streak"] = n if r > b else -n  # orange flame = win streak, blue = loss streak
    # your own HP: the big number left of the opponents' column in the player list (~24px tall at 1080p
    # and x ~130-140 of the list, theirs ~15px at x ~180). When it isn't read, say nothing — taking the
    # tallest number left gives someone else's HP (90 -> 75 -> 55: "lost 35 HP" on a win streak)
    plist = crop(frame, PLAYER_LIST)
    mine = [(hgt, int(t)) for hgt, x, _, t in reader.boxes(plist, "players")
            if t.strip().isdecimal() and int(t) <= 100 and hgt >= 0.019 * frame.shape[0]
            and x < 0.67 * plist.shape[1]]
    if mine:
        out["hp"] = max(mine)[1]
    return out


def _stage(round_):
    m = re.match(r"(\d)-(\d)", round_ or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def target_level(plan, round_):
    """Highest level the plan's leveling timeline expects by this round."""
    now = _stage(round_)
    best = None
    for stage, what in (plan or {}).get("leveling", []):
        s = _stage(stage)
        lv = re.search(r"Lv (\d+)", what)
        if s and lv and now and s <= now:
            best = max(best or 0, int(lv.group(1)))
    return best


def roll_level(plan):
    m = re.search(r"(\d+)", (plan or {}).get("roll_at", ""))
    return int(m.group(1)) if m else None


def advise(econ, round_, plan, progress=None):
    """-> (headline, detail) or None when there's not enough on screen to say anything.
    progress: [(unit, copies bought)] for a reroll comp's cheap carries — no leveling past the roll
    level until they're 3★ (9 copies). econ["hp_lost"]: HP the last combat cost (the app tracks it)."""
    gold, level, hp, streak = econ.get("gold"), econ.get("level"), econ.get("hp"), econ.get("streak") or 0
    lost = econ.get("hp_lost") or 0
    if gold is None or level is None:
        return None
    st = _stage(round_) or (0, 0)
    interest = min(gold // 10, 5)
    next_bp = min(50, (gold // 10 + 1) * 10)
    xp_left = None
    if econ.get("xp") is not None and econ.get("xp_need"):
        xp_left = max(0, econ["xp_need"] - econ["xp"])
    level_cost = math.ceil(xp_left / 4) * 4 if xp_left is not None else None
    style = (plan or {}).get("style", "")
    reroll = "Reroll" in style or "reroll" in style.lower()
    rl = roll_level(plan)
    tgt = target_level(plan, round_)

    # 1. stabilise first: early 3★ boards (Solar, Riftbeast) end games from ~20 HP, so this starts at
    #    45 HP from 3-1 — and after any round that cost 15+ HP, whatever's left. Not on a win streak:
    #    a board that keeps winning is stable
    winning = streak >= 2
    if not winning and hp is not None and hp <= 45 and st >= (3, 1):
        return ("Stabilise: roll now", f"{hp} HP — " + (
            "spend down to stay alive; econ doesn't matter if you're out." if hp <= 30 else
            "early 3★ boards finish you from ~20: roll for 2★ upgrades now, not later."))
    if not winning and lost >= 15:
        return ("Stabilise: roll now", f"Lost {lost} HP last round" + (f" ({hp} left)" if hp is not None else "")
                + " — roll for 2★ upgrades before the next fight.")

    # 2. losing every round: make the board strong now. A loss streak only pays in stage 2 with
    #    lots of HP; from stage 3 a weak board just bleeds out (and the comp may need changing).
    if streak <= -3:
        if st < (3, 1) and (hp is None or hp > 70):
            return (f"Loss streak ({-streak}): fine until 3-1",
                    "Bank gold now, but plan to spend at 3-1 on a board that wins rounds.")
        at_roll = reroll and rl and level >= rl  # reroll at its roll level: roll, don't level
        spend = (f"{level_cost}g → Lv {level + 1}, then " if level_cost is not None and level < 7
                 and gold >= level_cost and not at_roll else "")
        todo = (f"{spend}roll for 2★ on the units you hold. If your comp's core units haven't shown, "
                f"switch to what your board already is.")
        return (f"Strengthen the board now ({-streak} losses)", todo[0].upper() + todo[1:])

    # 3. reroll comp at its roll level: slow roll above 50, and stay there until the 3★s are done — or
    #    until the chase's cut-off: then a bench full of copies and a level-6 board just lose
    if reroll and rl and level >= rl:
        prog = progress or []
        tally = " · ".join(f"{u} {min(n, 9)}/9" for u, n in prog)
        m = re.search(r"(\d)-cost", style)
        cut = CHASE_UNTIL.get(int(m.group(1))) if m else None
        if prog and not all(n >= 9 for _, n in prog) and cut and st >= cut:
            if level_cost is not None and gold >= level_cost:
                return (f"Stop the 3★ chase: level up ({level_cost}g → Lv {level + 1})",
                        f"{tally} by {round_}. Sell spare copies, level and roll for 2★ upgrades.")
            return ("Stop the 3★ chase: save for levels", f"{tally} by {round_}. Sell spare copies; next gold "
                                                         f"goes into XP.")
        if prog and all(n >= 9 for _, n in prog):
            if level_cost is not None and gold >= level_cost:
                return (f"3★ done: level up ({level_cost}g → Lv {level + 1})", f"{tally}. Push levels for the rest "
                                                                             f"of the board now.")
            return ("3★ done: save for levels", f"{tally}. Next gold goes into XP.")
        stay = f"Stay Lv {level} until 3★ ({tally}). " if prog else ""
        if gold > 50:
            return (f"Slow roll {gold - 50}g", f"{stay}Roll only the gold above 50 (keep 50 for 5 interest).")
        return ("Don't roll — save to 50", f"{stay}{gold}g: {interest} interest now, {next_bp}g gives "
                                           f"{min(next_bp // 10, 5)}.")

    # 4. behind the plan's leveling timeline
    if tgt and level < tgt and level_cost is not None:
        after = gold - level_cost
        if after >= 0:
            keeps = min(after // 10, 5)
            if streak >= 2 or after >= 50 or keeps == interest or (hp is not None and hp <= 50):
                why = (f"win streak {streak}" if streak >= 2 else f"{hp} HP" if hp is not None and hp <= 50
                       else "keeps your interest")
                return (f"Level now: {level_cost}g → Lv {level + 1}",
                        f"Plan wants Lv {tgt} by {round_} ({why}); {after}g left = {keeps} interest.")
            return (f"Level soon ({level_cost}g → Lv {level + 1})",
                    f"Plan wants Lv {tgt} by {round_}, but it drops you to {after}g "
                    f"({keeps} interest). Level next round after interest.")
        return ("Save for the level", f"Need {level_cost}g to hit Lv {level + 1} (plan: Lv {tgt} by {round_}).")

    # 5. level/fast 9 comps at their roll-down level, stage 4+
    if not reroll and rl and level >= rl and st >= (4, 1) and gold >= 30:
        return ("Roll down now", f"Lv {level} is this comp's roll level — find the 2★ carries.")

    # 6. win streak: push levels to stay ahead
    if streak >= 3 and level_cost is not None and gold - level_cost >= 10:
        return (f"Push a level ({level_cost}g)", f"Win streak {streak}: stay the strongest board and keep it.")

    # 7. otherwise: build interest
    if gold < 50:
        return ("Hold gold", f"{gold}g = {interest} interest; {next_bp}g gives {min(next_bp // 10, 5)}.")
    return ("Econ is maxed", f"{gold}g: spend the excess above 50 on XP or rolls as the plan says.")
