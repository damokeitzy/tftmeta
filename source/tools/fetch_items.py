"""Build item data + icons from the Community Dragon dump. Run after re-downloading
data/cdragon_tft.json for a new patch.

- data/items.json:   components + completed items (recipes, icon files)
- data/special.json: set artifacts etc. offered in the armory ({name: icon file})
- data/radiant.json: Radiant items ({api: {name, icon, of: the item it upgrades}})
- data/item_names.json: every offerable item name, for OCR matching of armory cards
"""
import os
import json
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("TFTMETA_ROOT") or Path(__file__).resolve().parent.parent)
ICONS = ROOT / "data" / "icons"
CDN = "https://raw.communitydragon.org/latest/game/"

dump = json.loads((ROOT / "data" / "cdragon_tft.json").read_text(encoding="utf8"))
by_api = {i["apiName"]: i for i in dump["items"] if i.get("apiName")}

completed = {a: i for a, i in by_api.items()
             if a.startswith("TFT_Item_") and "Corrupted" not in a  # old-set variants
             and not (i["name"] or "tft_item_name_").startswith("tft_item_name_")  # placeholders
             and len(i.get("composition") or []) == 2
             and all(c in by_api for c in i["composition"])}
component_ids = {c for i in completed.values() for c in i["composition"]}


def icon_url(i):
    return CDN + i["icon"].lower().replace(".tex", ".png")


def download(url, dest):
    if dest.exists():
        return
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:  # missing art shouldn't stop the rest
        print("no icon:", dest.name, e)


ICONS.mkdir(parents=True, exist_ok=True)

items = {}
for api in sorted(component_ids | set(completed)):
    i = by_api[api]
    items[api] = {"name": i["name"], "components": i.get("composition") or [], "icon": api + ".png"}
    download(icon_url(i), ICONS / items[api]["icon"])

# this set's trait emblems (Spatula / Frying Pan + component) — recipes from the set's DA_ entries,
# mapped onto the standard component ids by name
comp_by_name = {by_api[c]["name"].lower(): c for c in component_ids}
for api, i in by_api.items():
    if api.startswith("DA_") and (i.get("name") or "").endswith(" Emblem") and len(i.get("composition") or []) == 2:
        parts = [comp_by_name.get((by_api.get(c) or {}).get("name", "").lower()) for c in i["composition"]]
        if all(parts) and i.get("icon"):
            items[api] = {"name": i["name"], "components": parts, "icon": api + ".png"}
            download(icon_url(i), ICONS / items[api]["icon"])

# Set data assets ("DA_...") hold this set's artifacts, emblems, consumables
set_traits = set(json.loads((ROOT / "data" / "set.json").read_text(encoding="utf8"))["traits"])
special = {}
for api, i in by_api.items():
    if api.startswith("DA_Artifact_") and i.get("name") and i.get("icon"):
        special[i["name"]] = api + ".png"
        download(icon_url(i), ICONS / special[i["name"]])
    # trait emblems you can't craft (Defender, Juggernaut, ...): carousel / armory / augments only. Only real
    # traits (not the Phantom Emblem consumable), first entry per name (DA_18_EmblemX before its ...Augment twin)
    elif api.startswith("DA_") and (i.get("name") or "").endswith(" Emblem") and not i.get("composition") \
            and i.get("icon") and i["name"] not in {it["name"] for it in items.values()} \
            and i["name"][:-len(" Emblem")] in set_traits and i["name"] not in special:
        special[i["name"]] = api + ".png"
        download(icon_url(i), ICONS / special[i["name"]])

# Radiant items (Tocker's rewards, radiant augments): their own art, but they finish the item they upgrade,
# so the item reader counts a Radiant Deathblade as Deathblade. This set's DA_ entry first, else the older one
radiant = {}
for base, it in items.items():
    if base not in completed:
        continue
    found = sorted((a for a, i in by_api.items() if i.get("name") == "Radiant " + it["name"] and i.get("icon")),
                   key=lambda a: not a.startswith("DA_"))
    if found:
        radiant[found[0]] = {"name": "Radiant " + it["name"], "icon": found[0] + ".png", "of": base}
        download(icon_url(by_api[found[0]]), ICONS / radiant[found[0]]["icon"])

# real items only (augments/consumables are excluded so their pick screens aren't mistaken
# for an item choice)
names = sorted({it["name"] for it in items.values()} | set(special) |
               {i["name"] for a, i in by_api.items()
                if a.startswith("DA_") and (i.get("name") or "").endswith(" Emblem")})



def stat_tags(effects):
    """Coarse stat profile used to match items to carries: AP / AD / AS / TANK / MANA."""
    e = {k: v for k, v in (effects or {}).items() if isinstance(v, (int, float))}
    tags = set()
    if e.get("AP", 0) >= 15:
        tags.add("AP")
    if 0.1 <= e.get("AD", 0) < 5 or e.get("AD", 0) >= 15 or e.get("ADOnAttack", 0) > 0:  # AD is a fraction
        tags.add("AD")
    if e.get("AS", 0) >= 10:
        tags.add("AS")
    if e.get("Health", 0) >= 150 or e.get("Armor", 0) >= 20 or e.get("MagicResist", 0) >= 20 \
            or e.get("BonusPercentHP", 0) > 0 or e.get("BaseResistsPerStack", 0) > 0:
        tags.add("TANK")
    if e.get("Mana", 0) >= 10 or e.get("FlatManaRestore", 0) > 0:
        tags.add("MANA")
    return sorted(tags)


# stats live on whichever entry of a name has effects filled in (artifacts' DA_ entries are empty)
stats = {}
for i in dump["items"]:
    name = i.get("name")
    if name in names:
        tags = stat_tags(i.get("effects"))
        if len(tags) > len(stats.get(name, [])):
            stats[name] = tags

(ROOT / "data" / "item_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf8")
(ROOT / "data" / "items.json").write_text(json.dumps(items, indent=1), encoding="utf8")
(ROOT / "data" / "special.json").write_text(json.dumps(special, indent=1), encoding="utf8")
(ROOT / "data" / "radiant.json").write_text(json.dumps(radiant, indent=1), encoding="utf8")
(ROOT / "data" / "item_names.json").write_text(json.dumps(names, indent=1), encoding="utf8")
print(f"{len(component_ids)} components, {len(completed)} completed, {len(special)} artifacts, "
      f"{len(radiant)} radiant, {len(names)} names")
