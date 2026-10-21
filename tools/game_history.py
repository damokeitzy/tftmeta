"""Your game history (kept by the overlay in data/games/history.jsonl): augments offered and taken, your
build, the round you went out on, HP / level by stage, what you bought.

  py tools/game_history.py        the last 10 games
  py tools/game_history.py 30     the last 30
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAMES = ROOT / "data" / "games"


def show(g, label=""):
    rounds = g.get("rounds") or {}
    stage_hp = " · ".join(f"{r} {v['hp']}hp Lv{v.get('level') or '?'}" for r, v in sorted(rounds.items())
                          if r.endswith("-1") and v.get("hp") is not None)
    out = g.get("last_round", "?")
    print(f"{g.get('started', '?')}{label}  went out {out}"
          + (f"  (overlay started at {g['first_round']})" if g.get("partial") else ""))
    print(f"  build: {g.get('build', '?')}" + (" (pinned)" if g.get("pinned") else " (not pinned)"))
    for a in g.get("augments") or []:
        others = [o for o in a.get("offered", []) if o != a.get("took")]
        print(f"  {a['round']}: took {a.get('took') or '? (not read)'}" + (f"   — also offered {', '.join(others)}" if others else ""))
    if stage_hp:
        print(f"  HP: {stage_hp}")
    bought = sorted((g.get("bought") or {}).items(), key=lambda kv: -kv[1])
    if bought:
        print("  bought: " + ", ".join(f"{u} ×{n}" for u, n in bought[:10]))
    print()


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    path = GAMES / "history.jsonl"
    games = [json.loads(l) for l in path.read_text(encoding="utf8").splitlines() if l.strip()] if path.exists() else []
    current = GAMES / "current.json"
    if current.exists():
        show(json.loads(current.read_text(encoding="utf8")), "  [in progress]")
    for g in reversed(games[-n:]):
        show(g)
    if not games and not current.exists():
        print("No games recorded yet — the overlay records them as you play.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf8")
    main()
