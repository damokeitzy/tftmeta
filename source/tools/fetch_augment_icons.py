"""Download this set's augment icons from Community Dragon, for reading the augments you took off the
little box above the lamp on the left of your board (tft_overlay/augment_lamp.py).

  py tools/fetch_augment_icons.py      after data/augment_names.json / cdragon_tft.json change (new patch)

-> data/augment_icons/<api>.png and data/augment_icons.json ({augment name: icon file}).
"""
import os
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("TFTMETA_ROOT") or Path(__file__).resolve().parent.parent)
OUT = ROOT / "data" / "augment_icons"
CDN = "https://raw.communitydragon.org/latest/game/"


def main():
    dump = json.loads((ROOT / "data" / "cdragon_tft.json").read_text(encoding="utf8"))
    names = json.loads((ROOT / "data" / "augment_names.json").read_text(encoding="utf8"))
    entries = {}
    for i in dump["items"]:  # this set's entry (DA_...) first, else any entry with that name and an icon
        n = i.get("name")
        if n in names and i.get("icon") and (n not in entries or i["apiName"].startswith("DA_")):
            entries[n] = i
    OUT.mkdir(parents=True, exist_ok=True)
    index, missing = {}, []
    for n, i in sorted(entries.items()):
        dest = OUT / (i["apiName"] + ".png")
        if not dest.exists():
            try:
                url = CDN + i["icon"].lower().replace(".tex", ".png")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                dest.write_bytes(urllib.request.urlopen(req, timeout=20).read())
            except Exception as e:  # missing art shouldn't stop the rest
                missing.append(n)
                print("no icon:", n, e, flush=True)
                continue
        index[n] = dest.name
    (ROOT / "data" / "augment_icons.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf8")
    print(f"{len(index)} augment icons, {len(missing)} missing, {len(set(names) - set(entries))} names without art")


if __name__ == "__main__":
    sys.exit(main())
