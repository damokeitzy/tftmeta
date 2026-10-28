"""Your game history, kept on this PC: one line per game in data/games/history.jsonl — the augments you
were offered (rerolls too) and took, your build (pinned, or the top suggestion if you never pinned), the
round you went out on, your HP / level / gold at the start of each round, your final traits and what you
bought. `py tools/game_history.py` shows it.

The game in progress is kept in data/games/current.json and saved as it goes, so a restart of the overlay
(within 15 minutes) carries on with the same game, and a crash loses nothing: next start files it away.
A game ends when the next one starts (back to stage 1) or the TFT window has been gone for 2 minutes.
"""
import json
import time

from .gamedata import DATA

DIR = DATA / "games"
CURRENT = DIR / "current.json"
HISTORY = DIR / "history.jsonl"
CONTINUE_S = 15 * 60  # an overlay restart within this long carries on with the same game


def _key(round_):
    """'3-10' -> (3, 10): rounds in play order (as text '3-10' sorts before '3-8')."""
    stage, _, n = round_.partition("-")
    return (int(stage), int(n)) if stage.isdigit() and n.isdigit() else (0, 0)


class GameLog:
    def __init__(self):
        DIR.mkdir(parents=True, exist_ok=True)
        self.game = None
        if CURRENT.exists():
            try:
                g = json.loads(CURRENT.read_text(encoding="utf8"))
            except ValueError:
                g = None
            if g and time.time() - g.get("updated", 0) < CONTINUE_S:
                self.game = g  # the overlay restarted mid-game: carry on with it
            elif g:
                self._archive(g)

    def _archive(self, g):
        if g.get("rounds"):
            with HISTORY.open("a", encoding="utf8") as f:
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
        CURRENT.unlink(missing_ok=True)

    def finish(self):
        """The game is over: file it in the history."""
        if self.game:
            self.game["ended"] = time.strftime("%Y-%m-%d %H:%M")
            self._archive(self.game)
        self.game = None

    def update(self, round_, hp=None, level=None, gold=None, build=None, pinned=False, traits=None,
               owned=None, augments=None):
        """After each full read of your own board."""
        if not round_:
            return
        g = self.game
        if g and round_.startswith("1-") and g.get("last_round", "") >= "2-1":
            self.finish()  # back to stage 1: a new game
            g = None
        if g is None:
            g = self.game = {"started": time.strftime("%Y-%m-%d %H:%M"), "first_round": round_,
                             "partial": round_ > "2-1", "rounds": {}, "augments": []}
        changed = round_ != g.get("last_round") or (augments is not None and augments != g["augments"])
        g["last_round"] = max(round_, g.get("last_round", round_), key=_key)  # Tocker's: 3-10 after 3-8
        if round_ not in g["rounds"] and hp is not None:
            g["rounds"][round_] = {"hp": hp, "level": level, "gold": gold}  # at the round's first read
        if build:
            g["build"], g["pinned"] = build, pinned
        if traits:
            g["traits"] = traits
        if owned:
            g["bought"] = owned
        if augments is not None:
            g["augments"] = augments
        g["updated"] = time.time()
        if changed:  # once per round (and per augment), not every read
            CURRENT.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf8")
