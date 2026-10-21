"""Keep the game data in step with the live patch, automatically.

  py tools/update_game_data.py          say whether Community Dragon has a newer patch, change nothing
  py tools/update_game_data.py --sync   when it does: download the dump (data/cdragon_tft.json), then redo
                                        set.json + champion icons (extract_set.py), items + item icons
                                        (fetch_items.py), this set's augment names / descriptions, and the
                                        augment icons (fetch_augment_icons.py). Last line: "SYNC {...}".
                                        The overlay runs this first at startup and restarts itself after an
                                        update.

Community Dragon publishes a patch's game files a few hours after it goes live; the check is one small
request (content-metadata.json) per start. The version done last is in data/game_data_version.json; the
previous dump is kept as data/cdragon_tft.prev.json.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CDN = "https://raw.communitydragon.org/latest/"
UA = {"User-Agent": "Mozilla/5.0"}


def http(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def live_version():
    return json.loads(http(CDN + "content-metadata.json"))["version"]


def newest_set(dump):
    """The newest standard set in the dump ("TFTSet18"), not PAIRS / TURBO / event variants."""
    sets = [s["mutator"] for s in dump["setData"] if re.fullmatch(r"TFTSet\d+", s.get("mutator") or "")]
    return max(sets, key=lambda m: int(m[6:]))


def render(item):
    """An augment's description with its numbers filled in ('Gain @InstantGold@ gold' -> 'Gain 4 gold')."""
    text = re.sub(r"<[^>]+>", "", item.get("desc") or "")
    effects = item.get("effects") or {}

    def number(m):
        v = effects.get(m.group(1))
        if not isinstance(v, (int, float)):
            return "#"
        return "%g" % (round(v * 100, 4) if m.group(2) else v)
    return re.sub(r"\s+", " ", re.sub(r"@([^@*]+)(\*100)?@", number, text)).strip()


def augment_data(dump, mutator):
    """This set's augments (its DA_ entries): sorted names, {name: description}."""
    s = next(x for x in dump["setData"] if x["mutator"] == mutator)
    by_api = {i["apiName"]: i for i in dump["items"] if i.get("apiName")}
    descs = {}
    for api in s.get("augments") or []:
        i = by_api.get(api)
        if api.startswith("DA_") and i and i.get("name"):
            descs.setdefault(i["name"], render(i))
    return sorted(descs), descs


def run_tool(*args):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / args[0]), *args[1:]], capture_output=True, text=True,
                       encoding="utf8", errors="replace", cwd=str(ROOT))
    print(r.stdout.strip()[-400:], flush=True)
    if r.returncode:
        raise RuntimeError(f"{args[0]} failed: {(r.stderr or r.stdout).strip()[-300:]}")


def main():
    sync = "--sync" in sys.argv
    vpath = DATA / "game_data_version.json"
    have = json.loads(vpath.read_text(encoding="utf8")).get("version") if vpath.exists() else None
    live = live_version()
    print(f"game data: have {have}, Community Dragon has {live}", flush=True)
    if live == have or not sync:
        if sync:
            print("SYNC " + json.dumps({"added": [], "changed": [], "removed": [], "failed": 0, "total": 0,
                                        "updated": False}), flush=True)
        return
    tmp = DATA / "cdragon_tft.json.tmp"
    tmp.write_bytes(http(CDN + "cdragon/tft/en_us.json", timeout=180))
    dump = json.loads(tmp.read_text(encoding="utf8"))  # a broken download stops here, nothing replaced
    mutator = newest_set(dump)
    current = DATA / "cdragon_tft.json"
    if current.exists():
        current.replace(DATA / "cdragon_tft.prev.json")
    tmp.replace(current)
    names, descs = augment_data(dump, mutator)
    (DATA / "augment_names.json").write_text(json.dumps(names, indent=1, ensure_ascii=False), encoding="utf8")
    (DATA / "augments.json").write_text(json.dumps(descs, indent=1, ensure_ascii=False), encoding="utf8")
    run_tool("extract_set.py", mutator)
    run_tool("fetch_items.py")
    run_tool("fetch_augment_icons.py")
    vpath.write_text(json.dumps({"version": live, "set": mutator, "updated": time.strftime("%Y-%m-%d %H:%M")},
                                indent=1), encoding="utf8")
    print("SYNC " + json.dumps({"added": [], "changed": [f"game data {have} -> {live} ({mutator})"], "removed": [],
                                "failed": 0, "total": 1, "updated": True}), flush=True)


if __name__ == "__main__":
    main()
