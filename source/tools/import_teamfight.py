"""Import exact comps from teamfight.lol team-builder links.

Sources (either or both):
  py tools/import_teamfight.py                 links in data/teamfight_links.txt
  py tools/import_teamfight.py --bunnymuffins  every comp linked on bunnymuffins.lol/meta,
                                               with its tier from that page
  add --refresh to re-download comps already imported
  py tools/import_teamfight.py --sync          make the builds match bunnymuffins.lol/meta right now:
                                               new links imported, every build re-fetched (changes on
                                               teamfight.lol picked up), tiers from the page, builds no
                                               longer listed removed. Writes once at the end; the last
                                               line is "SYNC {json summary}". The overlay runs this at
                                               startup (custom_comps.json is never touched).

Each comp keeps teamfight.lol's exact board: hex position (1-28, 4 rows x 7), 3★ flags and the
items on every unit, plus its best augments. Variants ("— Standard", "— Vanguard") are separate
builds. Requests are throttled and progress is saved after every comp, so a rate limit (HTTP 429)
just means: run it again later and it carries on.
"""
import html as htmllib
import os
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("TFTMETA_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))
from tft_overlay.plans import make_plan  # noqa: E402

API = "https://api.teamfight.lol/graphql"
BUNNY = "https://bunnymuffins.lol/meta/"
QUERY = ("query GetTFTComp($uuid: String!) { getTftComp(uuid: $uuid) "
         "{ uuid compName hexPositions bestAugments set updatedAt } }")
AUG_TIERS = {"TIER_1": "Silver", "TIER_2": "Gold", "TIER_3": "Prismatic"}
TIER_MAP = {"S": "S", "A": "A", "B": "B", "C": "C", "Special": "Special",
            "Very Special": "Special", "Not Special": "Special"}
UA = {"User-Agent": "Mozilla/5.0"}
PAUSE_S = 1.5


def http(url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=25).read()


def fetch(uuid):
    body = json.dumps({"query": QUERY, "variables": {"uuid": uuid}}).encode()
    for attempt in range(6):
        try:
            data = json.loads(http(API, body, {"Content-Type": "application/json",
                                               "Origin": "https://teamfight.lol"}))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            wait = 15 * (attempt + 1)
            print(f"  rate limited, waiting {wait}s…", flush=True)
            time.sleep(wait)
    comp = (data.get("data") or {}).get("getTftComp")
    if not comp:
        raise ValueError(f"no comp for {uuid}: {data.get('errors')}")
    return comp


def bunnymuffins(known_names):
    """uuid -> {"title", "tier"} from bunnymuffins.lol/meta: the tier is the section the link is
    in, the title is the comp heading just before the link (matched against known comp names)."""
    page = http(BUNNY).decode("utf8", "replace")
    marks = [(m.start(), "tier", TIER_MAP[m.group(1)]) for m in
             re.finditer(r">\s*((?:Very |Not )?(?:S|A|B|C|Special))\s+Tier\b", page)]
    marks += [(m.start(), "comp", m.group(1)) for m in re.finditer(r"comp=([0-9a-f-]{36})", page)]
    out, tier = {}, None
    order = ["S", "A", "B", "C", "Special"]
    names = sorted(known_names, key=len, reverse=True)
    for pos, kind, val in sorted(marks):
        if kind == "tier":  # sections run S -> A -> B -> C -> Special; ignore "S Tier" in prose later on
            if tier is None or order.index(val) > order.index(tier):
                tier = val
            continue
        if val in out:
            continue
        ctx = htmllib.unescape(re.sub(r"<[^>]+>", " ", page[max(0, pos - 2500):pos])).replace("’", "'")
        title = None
        best = -1
        for n in names:  # the comp heading closest to the link
            i = ctx.rfind(n)
            if i > best:
                best, title = i, n
        out[val] = {"title": title, "tier": tier}
    return out


def snapshot_names(headings, champions):
    """{comp heading: bunnymuffins' name} from the page's tier list ("A: Riftbeast Reroll, Defender
    Cassiopeia, Invoker Ahri Morgana *, ...") — the names the player knows. Matched to the comp
    headings by shared words (champion names count most). Also {heading: True} for '*' = has a big
    requirement (emblem / specific augment)."""
    page = http(BUNNY).decode("utf8", "replace")
    text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page))).replace("’", "'")
    i = text.find("Best TFT Builds")
    seg = text[i:text.find("Comps to 1 Trick", i)] if i >= 0 else ""
    champs = {c.lower() for c in champions}

    def words(s):
        return set(re.findall(r"[a-z0-9']+", s.lower())) - {"and", "the"}

    def weight(w):
        return 2 if w in champs or w in {"kha'zix", "master", "yi"} else 0.5 if w in {"reroll", "fast", "flex", "9"} else 1
    names, star = {}, {}
    seg = seg.split(" Meta ", 1)[-1]
    for group in re.split(r"(?:^|\s)(?:[SABC]|Special):", seg):  # "S: a, b *, c, A: d, ..."
        for raw in group.split(","):
            alias = raw.strip()
            starred = alias.endswith("*")
            alias = alias.rstrip("* ").strip()
            if len(alias) < 4:
                continue
            best = max(headings, key=lambda h: sum(weight(w) for w in words(h) & words(alias)), default=None)
            if best and sum(weight(w) for w in words(best) & words(alias)) >= 1.5 and best not in names:
                names[best] = alias
                star[best] = starred
    return names, star


def card_fields(headings):
    """{comp heading: {"conditions": ..., "leveling": ...}} from each build's card on bunnymuffins:
    'Conditions:' ("2-1 to 2-3 Kha'Zix BIS Kha'Zix High tempo and lots of items") and 'Leveling
    Pattern:' ("3 Cost Reroll Lose Streak Round 2-3 2-7 3-2 3-5 (40g+) 5+ 6+ Level 4 5 6 7 8 9 ..."),
    written by the guide's author."""
    page = http(BUNNY).decode("utf8", "replace")
    text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page))).replace("’", "'")
    out = {}
    for h in headings:
        for m in re.finditer(re.escape(h), text):
            card = text[m.start():m.start() + 5000]
            if "Copy Team Code" not in card[:600]:
                continue  # a mention in prose, not the build's card
            c = re.search(r"Conditions\s*:\s*(.*?)\s*How to Play", card)
            lv = re.search(r"Leveling Pattern\s*:\s*(.*?)\s*(?:Conditions|How to Play)", card)
            out[h] = {"conditions": c.group(1).strip() if c else "", "leveling": lv.group(1).strip() if lv else ""}
            break
    for h, v in how_to_play(page, headings).items():  # found by its own heading, even without Conditions
        out.setdefault(h, {"conditions": "", "leveling": ""})["howto"] = v
    return out


def how_to_play(page, headings):
    """{comp heading: {gameplan, stages [[label, text]], tips, holders, emblem, items [[unit, text]],
    augments [[label, text]]}} from each card's 'How to Play' part on bunnymuffins."""
    def clean(s):
        return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).replace("’", "'").replace(
            " ,", ",").strip(" :")

    def lines(html):  # top-level '<li><strong>Label</strong>: text <ul>more</ul></li>' -> [[label, text]]
        return [[clean(a), clean(b) + (" (" + clean(c) + ")" if c and clean(c) else "")] for a, b, c in re.findall(
            r"<li[^>]*>\s*<strong>([^<]+)</strong>\s*:?(.*?)(?:<ul[^>]*>(.*?)</ul>)?\s*</li>", html or "", re.S)]

    def bold(html):  # the author's bold items are the must-haves: ['Crownguard', "Gargoyle's", 'Shojin/Blue']
        return [p.strip().rstrip("*").strip() for g in re.findall(r"<strong>(.*?)</strong>", html or "", re.S)
                for p in clean(g).split(",") if p.strip().rstrip("*").strip()]

    def starred(html):  # bold items marked with an asterisk in the text: "Gunblade*, Deathcap, Guinsoo's"
        return re.sub(r"<strong>(.*?)</strong>", lambda m: ", ".join(p + "*" for p in bold(m.group(0))) +
                      (", " if clean(m.group(1)).endswith(",") else ""), html or "", flags=re.S)

    def tidy(s):  # "Emblem*,Jeweled" -> "Emblem*, Jeweled"; "Night*, (Striker's" -> "Night* (Striker's"
        return re.sub(r",(?=\S)", ", ", re.sub(r",\s*\(", " (", s))

    def item_lines(html):  # [[unit, text with must-haves starred, [must-haves]]]
        return [[clean(a), tidy(clean(starred(b)) + (" (" + clean(starred(c)) + ")" if c and clean(c) else "")),
                 bold(b) + bold(c)] for a, b, c in re.findall(
            r"<li[^>]*>\s*<strong>([^<]+)</strong>\s*:?(.*?)(?:<ul[^>]*>(.*?)</ul>)?\s*</li>", html or "", re.S)]
    out = {}
    for chunk in page.split("<h3")[1:]:
        m = re.match(r"[^>]*>(.*?)</h3>", chunk, re.S)
        h = clean(m.group(1)) if m else ""
        k = chunk.find("How to Play")
        if h not in headings or h in out or k < 0:
            continue
        sec = chunk[k:]
        gp = re.search(r"<strong>\s*Gameplan:?\s*</strong>(.*?)(?:<ul|</li>)", sec, re.S)
        st = re.search(r"<strong>\s*Gameplan:?\s*</strong>.*?<ul[^>]*>(.*?)</ul>", sec, re.S)
        tp = re.search(r"<strong>\s*Tips:?\s*</strong>\s*:?\s*<ul[^>]*>(.*?)</ul>", sec, re.S)
        hold = re.search(r"Item Holders?:?\s*</strong>(.*?)(?:<br|</p>)", sec, re.S)
        emb = re.search(r"Best Emblem/Spatula Holder:?\s*</strong>(.*?)(?:<br|</p>)", sec, re.S)
        its = re.search(r"<strong>\s*Items:?\s*</strong>.*?<ul[^>]*>(.*?)</ul>\s*(?:<p[^>]*>\s*<strong>\s*Augments|$)", sec, re.S)
        aug = re.search(r"<strong>\s*Augments\s*</strong>.*?<ul[^>]*>(.*?)</ul>", sec, re.S)
        out[h] = {"gameplan": clean(gp.group(1)) if gp else "",
                  "stages": [x for x in lines(st.group(1) if st else "") if x[0].lower().startswith("stage")],
                  "tips": [clean(t) for t in re.findall(r"<li[^>]*>(.*?)</li>", tp.group(1) if tp else "", re.S)],
                  "holders": clean(hold.group(1)) if hold else "",
                  "emblem": clean(emb.group(1)) if emb else "",
                  "items": item_lines(its.group(1) if its else ""),
                  "augments": lines(aug.group(1) if aug else "")}
    return out


def main():
    sync = "--sync" in sys.argv
    if sync and "--bunnymuffins" not in sys.argv:
        sys.argv.append("--bunnymuffins")
    refresh = "--refresh" in sys.argv or sync
    dump = json.loads((ROOT / "data" / "cdragon_tft.json").read_text(encoding="utf8"))
    names = {i["apiName"]: i.get("name") for i in dump["items"] if i.get("apiName")}
    s18 = next(x for x in dump["setData"] if x["mutator"] == "TFTSet18")
    champ_by_api = {c["apiName"]: c for c in s18["champions"]}
    set_data = json.loads((ROOT / "data" / "set.json").read_text(encoding="utf8"))
    items = json.loads((ROOT / "data" / "items.json").read_text(encoding="utf8"))
    item_by_name = {it["name"].lower(): it["name"] for it in items.values()}
    special = json.loads((ROOT / "data" / "special.json").read_text(encoding="utf8"))

    comps_path = ROOT / "data" / "comps.json"
    store = json.loads(comps_path.read_text(encoding="utf8")) if comps_path.exists() else {"comps": []}
    base_of = lambda n: n.split(" — ")[0].strip()  # noqa: E731
    norm = lambda n: base_of(n).lower().replace("’", "'").replace(" & ", " and ")  # noqa: E731
    existing = {norm(c["name"]): c for c in store["comps"]}

    links = (ROOT / "data" / "teamfight_links.txt").read_text(encoding="utf8")
    uuids = [] if sync else re.findall(r"comp=([0-9a-f-]{36})", links)  # sync: bunnymuffins only
    before = {c["name"]: c for c in store["comps"]}
    changed, failed, dead = [], [], []
    meta = {}
    if "--bunnymuffins" in sys.argv:
        known = {base_of(c["name"]) for c in store["comps"]} | {
            "Azir Rammus Reroll", "Kha'Zix Reroll", "Draven Fast 9", "Riftbeast Reroll", "Aphelios Flex",
            "Cassiopeia Reroll", "Invoker Morgana and Ahri", "Solar Yunara", "Elderwood Executioners",
            "Veigar Reroll", "Solar Akali Reroll", "Dragon Fast 9", "Ashe Fast 9", "Kayle Reroll",
            "Juggernaut Flex", "Flora Malphite", "Blossom Ahri", "Adaptor Reroll", "Caitlyn Reroll",
            "Primal Malphite", "Invoker Nidalee", "Tristana Rengar Reroll"}
        meta = bunnymuffins(known)
        uuids += list(meta)
        print(f"bunnymuffins: {len(meta)} comp links", flush=True)
    uuids = list(dict.fromkeys(uuids))
    done = {re.search(r"comp=([0-9a-f-]{36})", c.get("source", "") or "x") and
            re.search(r"comp=([0-9a-f-]{36})", c["source"]).group(1) for c in store["comps"]}

    for n, uuid in enumerate(uuids, 1):
        if uuid in done and not refresh:
            continue
        time.sleep(PAUSE_S)
        try:
            tc = fetch(uuid)
        except ValueError as e:  # the link points at a build teamfight.lol no longer has
            print(f"[{n}/{len(uuids)}] {uuid[:8]} gone on teamfight.lol: {e}", flush=True)
            dead.append(uuid)
            continue
        except Exception as e:  # network trouble: keep going; a re-run picks up what's missing
            print(f"[{n}/{len(uuids)}] {uuid[:8]} failed: {e}", flush=True)
            failed.append(uuid)
            continue
        m = meta.get(uuid, {})
        comp_name = (tc["compName"].strip() or f"{m.get('title') or 'Unnamed'} — Standard").replace("’", "'")
        base = base_of(comp_name)
        board, carries = [], []
        for hp in tc["hexPositions"]:
            champ = champ_by_api.get(hp["championId"], {})
            # board props (e.g. DA_18_StonebarkTree) aren't champions: "Stonebark Tree"
            fallback = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"^DA_\d+_", "", hp["championId"]))
            name = (champ.get("name") or fallback).split(" (")[0]
            is_unit = name in set_data["champions"]
            its = []
            for it in hp["items"]:
                iname = names.get(it["apiName"]) or it["apiName"]
                its.append(item_by_name.get(iname.lower(), iname))
            board.append({"unit": name, "pos": hp["position"], "three": bool(hp["flags"].get("IS_THREE_STARRED")),
                          "items": its, "prop": not is_unit,
                          "icon": set_data["champions"][name]["icon"] if is_unit else None})
            if its and is_unit:
                carries.append((len(its), hp["position"], name, its))
        board.sort(key=lambda b: b["pos"])
        carries.sort(key=lambda c: (c[2].lower() not in comp_name.lower(), -c[0], -c[1]))
        old = existing.get(norm(comp_name), {})
        main_unit = carries[0][2] if carries else None
        main_cost = set_data["champions"].get(main_unit, {}).get("cost", 4)
        augs = [f"{AUG_TIERS.get(t, t)}: " + ", ".join(names.get(a) or a for a in lst)
                for t, lst in sorted(tc["bestAugments"].items()) if lst]
        comp = {
            "name": comp_name,
            "tier": m.get("tier") or old.get("tier", "B"),
            "source": f"https://teamfight.lol/team-builder?set={tc['set']}&comp={tc['uuid']}",
            "units": [b["unit"] for b in board if not b["prop"]],
            "board": board,
            "carries": {name: its for _, _, name, its in carries},
            "augments": augs,
            "notes": old.get("notes", ""),
            "plan": make_plan(comp_name, main_cost),
            "updated_at": tc.get("updatedAt"),
        }
        prev = before.get(comp_name)
        if prev and (prev.get("updated_at") not in (None, comp["updated_at"]) or prev.get("tier") != comp["tier"]
                     or prev.get("carries") != comp["carries"] or prev.get("board") != comp["board"]):
            changed.append(comp_name)
        # replace the same build (full name) and the old hand-written entry (bare base name);
        # other variants of the comp stay as separate builds
        store["comps"] = [c for c in store["comps"]
                          if c["name"].lower() != comp_name.lower()
                          and not (not c.get("board") and norm(c["name"]) == norm(comp_name))]
        store["comps"].append(comp)
        if not sync:  # sync writes once, at the end (the overlay may be reading this file)
            comps_path.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf8")  # progress
        unknown = [i for b in board for i in b["items"] if i not in item_by_name.values() and i not in special]
        print(f"[{n}/{len(uuids)}] {comp['tier']:<7} {comp_name}: carries {list(comp['carries'])}"
              + (f"  (unmatched items: {unknown})" if unknown else ""), flush=True)


    if sync:
        # builds bunnymuffins no longer lists go (only when the page answered and nothing failed to
        # download — never wipe builds because of a network hiccup)
        listed = set(meta) - set(dead)
        removed = []
        if listed and not failed:
            keep = []
            for c in store["comps"]:
                m = re.search(r"comp=([0-9a-f-]{36})", c.get("source", "") or "")
                if not m or m.group(1) not in listed:  # not on bunnymuffins (or not a guide at all)
                    removed.append(c["name"])
                else:
                    keep.append(c)
            store["comps"] = keep
        added = [c["name"] for c in store["comps"] if c["name"] not in before]
        try:  # the names bunnymuffins uses in its tier list ("Defender Cassiopeia")
            aliases, starred = snapshot_names({base_of(c["name"]) for c in store["comps"] if c.get("board")},
                                              set_data["champions"])
            if aliases:
                store["_aliases"], store["_starred"] = aliases, starred
            fields = card_fields({base_of(c["name"]) for c in store["comps"] if c.get("board")})
            if fields:
                store["_conditions"] = {h: f["conditions"] for h, f in fields.items() if f["conditions"]}
                store["_leveling"] = {h: f["leveling"] for h, f in fields.items() if f["leveling"]}
                store["_howto"] = {h: f["howto"] for h, f in fields.items() if f.get("howto")}
        except Exception as e:
            print(f"names: {e}", flush=True)
        store["_synced"] = time.strftime("%Y-%m-%d %H:%M")
        tmp = comps_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf8")
        tmp.replace(comps_path)  # atomic: the overlay never sees half a file
        print("SYNC " + json.dumps({"added": added, "changed": changed, "removed": removed,
                                    "failed": len(failed), "total": len(store["comps"])}), flush=True)


if __name__ == "__main__":
    main()
