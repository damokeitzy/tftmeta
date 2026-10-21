"""Real ranked results from SeeMeta's Set 18 tier list, used to cap every guide's tier letter.

  py tools/import_seemeta.py          print the comps and their results, change nothing
  py tools/import_seemeta.py --sync   save them to data/seemeta.json (last line "SYNC {json summary}").
                                      The overlay runs this at startup, after the guide syncs.

SeeMeta groups tens of thousands of ranked matches into comps and publishes, per comp: average place,
win rate, top-4 rate and its 9 units (no items or positions). gamedata.load_comps matches each guide build
to a SeeMeta comp by shared units (5+) and caps the guide's letter by the comp's real top-4 rate — so a
build only shows as S or A when it actually finishes top 4 that often. A build SeeMeta doesn't list (niche,
emblem builds) has no proof and shows at most B.
"""
import html as htmllib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://seemeta.com/en/tft/set-18/tier-list"


def fetch():
    """-> ([{name, tier, avg, win, top4, units}], "October 7, 2026 to October 19, 2026" or None)."""
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf8", "replace")
    text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page)))
    period = re.search(r"Data period\s*([A-Z][a-z]+ \d+, \d{4} to [A-Z][a-z]+ \d+, \d{4})", text)
    heads = [(m.start(), htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip())
             for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", page, re.S)]
    tiers = [(m.start(), m.group(1)) for m in re.finditer(r">\s*([SABCD]\+?)\s+tier comps", page)]
    comps = []
    for i, (pos, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else pos + 8000
        block = page[pos:end]
        units = [htmllib.unescape(a) for a in re.findall(r'alt="([^"]+)"', block)]
        btext = htmllib.unescape(re.sub(r"<[^>]+>", " ", block))
        m = re.search(r"Avg Place\s*([\d.]+).*?Winrate\s*([\d.]+)%.*?Top 4\s*([\d.]+)%", btext, re.S)
        if not (m and units):
            continue
        tier = next((t for p, t in reversed(tiers) if p < pos), None)
        comps.append({"name": name, "tier": tier, "avg": float(m.group(1)), "win": float(m.group(2)),
                      "top4": float(m.group(3)), "units": units})
    return comps, (period.group(1) if period else None)


def main():
    comps, period = fetch()
    for c in sorted(comps, key=lambda c: -c["top4"]):
        print(f"top4 {c['top4']:4.1f}%  avg {c['avg']:.2f}  {c['name']}", flush=True)
    if len(comps) < 10:  # a layout change, not a real result: keep the saved data
        raise SystemExit("Couldn't read SeeMeta's tier list (page layout changed?)")
    if "--sync" not in sys.argv:
        return
    path = ROOT / "data" / "seemeta.json"
    before = {c["name"]: c for c in json.loads(path.read_text(encoding="utf8"))["comps"]} if path.exists() else {}
    after = {c["name"]: c for c in comps}
    store = {"_source": URL, "_period": period, "_synced": time.strftime("%Y-%m-%d %H:%M"), "comps": comps}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf8")
    tmp.replace(path)  # atomic: the overlay never sees half a file
    print("SYNC " + json.dumps({"added": [n for n in after if n not in before],
                                "changed": [n for n in after if n in before and before[n] != after[n]],
                                "removed": [n for n in before if n not in after], "failed": 0,
                                "total": len(comps)}), flush=True)


if __name__ == "__main__":
    main()
