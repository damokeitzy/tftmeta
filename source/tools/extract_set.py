"""Extract a small set-data file from the Community Dragon dump.

Usage: py tools/extract_set.py [SET_MUTATOR]   (default TFTSet18)
Re-download the dump first when a new set/patch lands:
  curl -o data/cdragon_tft.json https://raw.communitydragon.org/latest/cdragon/tft/en_us.json
"""
import os
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("TFTMETA_ROOT") or Path(__file__).resolve().parent.parent)
CDN = "https://raw.communitydragon.org/latest/game/"
mutator = sys.argv[1] if len(sys.argv) > 1 else "TFTSet18"

dump = json.loads((ROOT / "data" / "cdragon_tft.json").read_text(encoding="utf8"))
s = next(x for x in dump["setData"] if x["mutator"] == mutator)

traits = {
    t["name"]: sorted({e["minUnits"] for e in t.get("effects", [])})
    for t in s["traits"]
}
champions = {}
for c in s["champions"]:
    if not c.get("traits"):
        continue
    name = c["name"].split(" (")[0]  # "Lux (Solar)" -> "Lux"
    stats = c.get("stats") or {}
    entry = champions.setdefault(name, {"cost": c["cost"], "traits": [], "icon": None,
                                        "range": stats.get("range"), "hp": stats.get("hp")})
    for t in c["traits"]:
        if t not in entry["traits"]:
            entry["traits"].append(t)
    if not entry["icon"] and c.get("squareIcon"):
        entry["icon"] = c["apiName"] + ".png"
        entry["_url"] = CDN + c["squareIcon"].lower().replace(".tex", ".png")

icons = ROOT / "data" / "champ_icons"
icons.mkdir(parents=True, exist_ok=True)
for name, entry in champions.items():
    url = entry.pop("_url", None)
    dest = icons / (entry["icon"] or "")
    if url and not dest.exists():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            dest.write_bytes(urllib.request.urlopen(req, timeout=20).read())
        except Exception as e:
            print("no icon:", name, e)
            entry["icon"] = None

out = {"set": mutator, "traits": traits, "champions": champions}
(ROOT / "data" / "set.json").write_text(json.dumps(out, indent=1), encoding="utf8")
print(f"{mutator}: {len(traits)} traits, {len(champions)} champions -> data/set.json")
