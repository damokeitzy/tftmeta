"""Import TFT Academy's comp tier list (tftacademy.com/tierlist/comps): builds written by pro players,
with their tier letter, exact final board (hex positions, 3★, items), style, best augments and stage tips.

  py tools/import_tftacademy.py          print what the page lists, change nothing
  py tools/import_tftacademy.py --sync   make data/comps_tftacademy.json match the page right now. Writes
                                         once at the end; the last line is "SYNC {json summary}". The
                                         overlay runs this at startup, after the bunnymuffins sync.

Only public guides (the page also carries members-only ones: skipped), and only their S and A builds:
bunnymuffins already covers the lower tiers, and more B reroll builds would crowd the S/A ones out of the
picks again. A build that is the same as a bunnymuffins one isn't shown twice: gamedata.load_comps keeps
whichever guide rates it higher.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tft_overlay.plans import make_plan  # noqa: E402

URL = "https://tftacademy.com/tierlist/comps"
UA = {"User-Agent": "Mozilla/5.0"}
GUIDE = "TFT Academy"
TIERS = {"S": "S", "A": "A", "B": "B", "C": "C", "X": "Special"}  # X = situational (augment / emblem)
KEEP = ("S", "A")
_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ID = re.compile(r"[A-Za-z_$][\w$]*")


def parse_js(s, i):
    """The JS object literal SvelteKit inlines in the page (unquoted keys, double-quoted strings),
    starting at s[i] -> (value, index after it)."""
    def ws(i):
        while s[i] in " \t\r\n":
            i += 1
        return i

    def string(i):
        j = i + 1
        while s[j] != '"':
            j += 2 if s[j] == "\\" else 1
        return json.loads(s[i:j + 1].replace("\\'", "'")), j + 1

    def val(i):
        i = ws(i)
        ch = s[i]
        if ch in "{[":
            close, out = ("}", {}) if ch == "{" else ("]", [])
            i = ws(i + 1)
            if s[i] == close:
                return out, i + 1
            while True:
                if close == "}":
                    i = ws(i)
                    if s[i] == '"':
                        key, i = string(i)
                    else:
                        m = _ID.match(s, i)
                        key, i = m.group(), m.end()
                    i = ws(i)
                    if s[i] != ":":
                        raise ValueError(f"expected ':' at {s[i:i + 30]!r}")
                    out[key], i = val(i + 1)
                else:
                    v, i = val(i)
                    out.append(v)
                i = ws(i)
                if s[i] == ",":
                    i += 1
                    continue
                if s[i] != close:
                    raise ValueError(f"expected {close!r} at {s[i:i + 30]!r}")
                return out, i + 1
        if ch == '"':
            return string(i)
        for word, v in (("true", True), ("false", False), ("null", None), ("void 0", None)):
            if s.startswith(word, i):
                return v, i + len(word)
        m = _NUM.match(s, i)
        if m:
            t = m.group()
            return (float(t) if any(c in t for c in ".eE") else int(t)), m.end()
        raise ValueError(f"unexpected {s[i:i + 30]!r}")

    return val(i)


def guides(page):
    """Every build on the tier list page: [{title, tier, finalComp, mainChampion, style, ...}]."""
    i = page.find("guides:[")
    if i < 0:
        raise ValueError("no guides on the page (layout changed?)")
    return parse_js(page, i + len("guides:"))[0]


def convert(g, lookup):
    """One TFT Academy guide -> a build in comps.json's format (board as teamfight.lol positions 1-28)."""
    names, champ_by_api, champions, item_by_name = lookup

    def unit(api):
        champ = champ_by_api.get(api, {})
        # board props (DA_Elderwood18_StonebarkTree) aren't champions: "Stonebark Tree"
        fallback = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"^DA_(?:[A-Za-z]*\d+_)?|\d+$", "", api))
        return (champ.get("name") or fallback).split(" (")[0]

    def item(api):
        n = names.get(api) or api
        return item_by_name.get(n.lower(), n)

    board = []
    for u in g.get("finalComp") or []:
        name = unit(u["apiName"])
        is_unit = name in champions
        board.append({"unit": name, "pos": u["boardIndex"] + 1, "three": u.get("stars") == 3,
                      "items": [item(a) for a in u.get("items") or []], "prop": not is_unit,
                      "icon": champions[name]["icon"] if is_unit else None})
    board.sort(key=lambda b: b["pos"])
    main = unit((g.get("mainChampion") or {}).get("apiName", ""))
    carries = sorted((b for b in board if b["items"] and not b["prop"]),
                     key=lambda b: (b["unit"] != main, -len(b["items"]), -b["pos"]))
    style = g.get("style") or ""
    m = re.match(r"(\d)-Cost Reroll", style)
    plan_name = "Reroll" if m else "Fast 9" if "Fast 9" in style else ""
    main_cost = int(m.group(1)) if m else champions.get(main, {}).get("cost", 4)
    title = (g.get("title") or g.get("metaTitle") or main).strip().replace("’", "'")
    augs = [names.get(a["apiName"]) or a["apiName"] for a in g.get("augments") or [] if not a.get("disabled")]
    tips = [f'{t.get("stage", "")}: {t.get("tip", "").strip()}' for t in g.get("tips") or [] if t.get("tip")]
    return {
        "name": f"{title} — {GUIDE}",
        "tier": TIERS.get(g.get("tier"), "B"),
        "guide": GUIDE,
        "main": main if main in champions else None,
        "source": f"{URL}#{g.get('id', '')}",
        "units": [b["unit"] for b in board if not b["prop"]],
        "board": board,
        "carries": {b["unit"]: b["items"] for b in carries},
        # its stage 2 opener (4 units, no positions) with the item holders: shown until 4-1
        "early": [{"unit": unit(u["apiName"]), "items": [item(a) for a in u.get("items") or []]}
                  for u in g.get("earlyComp") or [] if unit(u["apiName"]) in champions],
        "augments": [", ".join(augs)] if augs else [],
        "augments_tip": (g.get("augmentsTip") or "").strip(),
        "tips": tips,
        "style": style,
        # an emblem / artifact on the final board is its best case, not a requirement: TFT Academy puts
        # builds that depend on one in tier X ("Special")
        "special_items_optional": g.get("tier") != "X",
        "notes": "",
        "plan": make_plan(plan_name, main_cost),
        "updated_at": g.get("updated"),
    }


def lookups():
    dump = json.loads((ROOT / "data" / "cdragon_tft.json").read_text(encoding="utf8"))
    names = {i["apiName"]: i.get("name") for i in dump["items"] if i.get("apiName")}
    s18 = next(x for x in dump["setData"] if x["mutator"] == "TFTSet18")
    champ_by_api = {c["apiName"]: c for c in s18["champions"]}
    set_data = json.loads((ROOT / "data" / "set.json").read_text(encoding="utf8"))
    items = json.loads((ROOT / "data" / "items.json").read_text(encoding="utf8"))
    item_by_name = {it["name"].lower(): it["name"] for it in items.values()}
    return names, champ_by_api, set_data["champions"], item_by_name


def main():
    sync = "--sync" in sys.argv
    page = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=25).read().decode("utf8", "replace")
    patch = re.search(r'patch:"([^"]+)"', page)
    lookup = lookups()
    every = [convert(g, lookup) for g in guides(page) if g.get("isPublic") and g.get("finalComp")]
    comps = [c for c in every if c["tier"] in KEEP]
    for c in every:
        print(f"{c['tier']:<8} {c['name']}: carries {list(c['carries'])}"
              + ("" if c in comps else "  (not imported)"), flush=True)
    if not comps:
        raise ValueError("no S/A builds on the page (layout changed?): keeping the saved ones")
    path = ROOT / "data" / "comps_tftacademy.json"
    if not sync:
        return
    before = {c["name"]: c for c in json.loads(path.read_text(encoding="utf8"))["comps"]} if path.exists() else {}
    after = {c["name"]: c for c in json.loads(json.dumps(comps))}  # as saved (tuples -> lists), to compare
    added = [n for n in after if n not in before]
    removed = [n for n in before if n not in after]
    changed = [n for n in after if n in before and before[n] != after[n]]
    store = {"_source": f"{URL} (patch {patch.group(1) if patch else '?'}): written by the sync, edits are overwritten "
                        "— put your own builds in custom_comps.json.",
             "_synced": time.strftime("%Y-%m-%d %H:%M"), "comps": comps}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf8")
    tmp.replace(path)  # atomic: the overlay never sees half a file
    print("SYNC " + json.dumps({"added": added, "changed": changed, "removed": removed, "failed": 0,
                                "total": len(comps)}), flush=True)


if __name__ == "__main__":
    main()
