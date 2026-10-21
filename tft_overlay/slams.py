"""Item advice for a comp: what to slam, what to hold, what components it still needs,
and which offered item (carousel / armory) is worth the most.

Priority comes from comps.json order: the first carry listed is the main carry, and each
carry's items are listed best-first. So earlier "wants" matter more. One exception: the main
tank (the first carry whose items are mostly tank items) is itemised first. A stable frontline
wins the early rounds; the carry's items come after.
"""
from collections import Counter

ALIASES = {
    "qss": "quicksilver", "vow": "protector's vow", "spark": "ionic spark",
    "morello": "morellonomicon", "titan's": "titan's resolve", "gargoyle's": "gargoyle stoneplate",
    "kraken": "kraken's fury", "deathcap": "rabadon's deathcap", "bramble": "bramble vest",
    "sunfire": "sunfire cape", "striker's": "striker's flail", "nashor's": "nashor's tooth",
    "adaptive": "adaptive helm", "gunblade": "hextech gunblade", "guinsoo's": "guinsoo's rageblade",
    "shojin": "spear of shojin", "warmog's": "warmog's armor", "sterak's": "sterak's gage",
    "archangel's": "archangel's staff", "ie": "infinity edge", "hoj": "hand of justice",
}
GENERIC_TANK = ["warmog's armor", "gargoyle stoneplate", "dragon's claw", "bramble vest",
                "crownguard", "sunfire cape", "protector's vow", "spirit visage", "adaptive helm",
                "steadfast heart"]
SHORT = {"Needlessly Large Rod": "Rod", "Tear of the Goddess": "Tear", "Recurve Bow": "Bow",
         "B.F. Sword": "Sword", "Sparring Gloves": "Gloves", "Giant's Belt": "Belt",
         "Negatron Cloak": "Cloak", "Chain Vest": "Vest", "Frying Pan": "Pan"}


class ItemBook:
    def __init__(self, items, stats=None, specials=None):
        """items: data/items.json (components, completed, emblems). Artifacts (data/special.json)
        are added as "special" items: finished, but never built from components."""
        self.items = dict(items)
        if specials is None:
            from .gamedata import load_special
            specials = load_special()
        for name, icon in specials.items():
            self.items.setdefault(icon.rsplit(".", 1)[0], {"name": name, "components": [], "icon": icon,
                                                           "special": True})
        self.by_name = {it["name"].lower(): api for api, it in self.items.items()}
        if stats is None:
            from .gamedata import load_item_stats
            stats = load_item_stats()
        self.stats = {n.lower(): set(t) for n, t in stats.items()}  # name -> {AP, AD, AS, TANK, MANA}

    def tags(self, label):
        """Stat tags for an api or an item name."""
        name = self.name(label) if label in self.items else label
        return self.stats.get(name.lower(), set())

    def resolve(self, text):
        """'Shojin / Blue Buff' -> [api, api]; 'Generic tank' -> tank items; unknown -> []."""
        out = []
        for opt in text.split("/"):
            key = opt.strip().lower()
            if "tank" in key:
                out += [self.by_name[n] for n in GENERIC_TANK if n in self.by_name]
                continue
            key = ALIASES.get(key, key)
            if key in self.by_name:
                out.append(self.by_name[key])
                continue
            hits = [api for name, api in self.by_name.items() if key and key in name]
            if len(hits) == 1:
                out.append(hits[0])
        return out

    def recipe(self, api):
        """An item's components; [] for one this set's data doesn't know (a new patch's item, 'Iron Will')."""
        return (self.items.get(api) or {}).get("components") or []

    def is_special(self, api):
        return bool(self.items.get(api, {}).get("special"))

    def is_component(self, api):
        return api in self.items and not self.is_special(api) and not self.recipe(api)

    def is_built(self, api):
        """A finished item: completed from components, or an artifact."""
        return api in self.items and (self.is_special(api) or bool(self.recipe(api)))

    def name(self, api):
        return self.items[api]["name"]

    def short(self, api):
        return SHORT.get(self.name(api), self.name(api))


_CHAMPS = None


def _champions():
    global _CHAMPS
    if _CHAMPS is None:
        from .gamedata import load_set
        _CHAMPS = load_set()["champions"]
    return _CHAMPS


def main_tank(comp, book):
    """First melee carry whose listed items are mostly tank items (tank stats, no AD/AP), or None.
    None once the tank is stable (comp["_tank_stable"]): then the carries come first."""
    if comp.get("_tank_stable"):
        return None
    champs = _champions()
    for unit, item_texts in comp["carries"].items():
        if (champs.get(unit, {}).get("range") or 1) > 1.5 or unit in comp.get("_missing", ()):
            continue  # ranged: never the tank; not owned: can't wear items yet
        firsts = [book.resolve(t) for t in item_texts]
        firsts = [o[0] for o in firsts if o]
        tanky = sum("TANK" in book.tags(a) and not book.tags(a) & {"AD", "AP"} for a in firsts)
        if firsts and tanky * 2 >= len(firsts):
            return unit
    return None


def wants_for(comp, book):
    """[(unit, [alternative apis])] in priority order: the main tank's items, then the main
    carry's best item first, and so on."""
    wants = []
    tank = main_tank(comp, book)
    order = sorted(comp["carries"], key=lambda u: u != tank)  # stable: tank first, rest as listed
    for unit in order:
        for text in comp["carries"][unit]:
            options = book.resolve(text)
            if options:
                wants.append((unit, options))
    return wants


def split_held(held):
    """`held` is either a flat list of item apis or a list of per-unit item lists (what
    ItemReader.read_units returns). -> (per-unit lists, flat list). A flat list is treated as
    one item per unit, i.e. unknown grouping."""
    if held and isinstance(held[0], (list, tuple)):
        units = [list(u) for u in held]
    else:
        units = [[a] for a in held]
    return units, [a for u in units for a in u]


def unit_partials(comp, held_units, book, wants=None):
    """Components sitting on a unit next to finished items -> {carry: [components]}.

    A component on a unit is committed: the next component dropped on that unit combines with
    it. We don't know which champion holds which items, so match each unit to the carry whose
    BIS list shares the most of its finished items (Deathblade + Sword -> Yunara)."""
    wants = wants or wants_for(comp, book)
    finished = Counter(a for items in held_units for a in items if book.is_built(a))
    out = {}
    for items in held_units:
        parts = [a for a in items if book.is_component(a)]
        built = {a for a in items if book.is_built(a)}
        if not parts:
            continue
        owner = None
        if built:  # match by the finished items next to it
            overlap = Counter()
            for unit, options in wants:
                overlap[unit] += len(built & set(options))
            if overlap and overlap.most_common(1)[0][1] > 0:
                owner = overlap.most_common(1)[0][0]
        if owner is None:  # lone component: the highest-priority unfinished item that uses it
            left = Counter(finished)
            for unit, options in wants:
                done = next((o for o in options if left[o] > 0), None)
                if done:
                    left[done] -= 1
                elif parts[0] in book.recipe(options[0]):
                    owner = unit
                    break
        if owner:
            out.setdefault(owner, []).extend(parts)
    return out


def assign_holders(comp, held_units, book, wants=None):
    """{index into held_units: carry} — which carry each item-holding unit is, judged on the whole
    unit: 3 x (items that are on that carry's list) + how well the items' stats fit the carry.
    Best pairs first, each carry once. A unit full of off-list tank items is still the tank
    (Hecarim with Bramble / Dragon's Claw / Sunfire), not an open slot to keep itemising."""
    wants = wants or wants_for(comp, book)
    opts = {}
    for unit, options in wants:
        opts.setdefault(unit, set()).update(options)
    profiles = dict(carry_profiles(comp, book))
    pairs = []
    for i, items in enumerate(held_units):
        built = [a for a in items if book.is_built(a)]
        if not built:
            continue
        for carry, options in opts.items():
            prof = profiles.get(carry) or Counter()
            total = sum(prof.values()) or 1
            fit = sum(sum(prof[t] for t in book.tags(a)) / total for a in built) / len(built)
            pairs.append((3 * len(set(built) & options) + fit, len(built), i, carry))
    out, used = {}, set()
    for score, _, i, carry in sorted(pairs, reverse=True):
        if score > 0 and i not in out and carry not in used:
            out[i] = carry
            used.add(carry)
    return out


def _state(comp, bench, held, book):
    """Walk wants in priority order against what we have.

    held: flat item list or per-unit lists (see split_held). Components on units count as
    available — reserved for the carry holding them, whose items using them go first.
    Returns (slams [(unit, api)], open_wants [(idx, unit, options)], spare Counter, matched,
    track [(unit, api, 'done' | 'slam' | 'open' | 'full' | 'covered', the want's listed item)])."""
    held_units, held_flat = split_held(held)
    spare = Counter(a for a in list(bench) + held_flat if book.is_component(a))
    already = Counter(a for a in held_flat if book.is_built(a))
    wants = wants_for(comp, book)
    partials = unit_partials(comp, held_units, book, wants)
    # components held back for a unit's unfinished item; they only block *other* units
    # (a carry can still use its own reserved parts for a different item)
    reserved = {u: Counter(parts) for u, parts in partials.items()}
    if partials:
        # Finish what's already started before anything else: an item that uses a component
        # already sitting on a unit comes first overall (that slot is wasted until it's done),
        # then everything else in normal priority order.
        def uses_partial(w):
            return any(p in book.recipe(w[1][0]) for p in partials.get(w[0], ()))
        order = {id(w): i for i, w in enumerate(wants)}
        wants = sorted(wants, key=lambda w: (not uses_partial(w), order[id(w)]))
    # A unit holding 3 items is finished, whatever they are — you may have built something other
    # than the guide's BIS because of your components. Its carry gets no more item advice.
    holders = assign_holders(comp, held_units, book, wants)
    full = {holders[i] for i, items in enumerate(held_units) if len(items) >= 3 and i in holders}
    # an item counts for the champion holding it first (Hand of Justice on Diana fills Diana's slot,
    # even if Kha'Zix lists it earlier); items on anyone else still count for whoever wants them
    pre = {}
    for i, carry in holders.items():
        for a in held_units[i]:
            if book.is_built(a) and already[a] > 0:
                k = next((n for n, (u, opts) in enumerate(wants) if u == carry and a in opts and n not in pre), None)
                if k is not None:
                    pre[k] = a
                    already[a] -= 1
    slams, open_wants, matched = [], [], 0
    track = []  # per want, for the icon panel
    for idx, (unit, options) in enumerate(wants):
        done = pre.get(idx)
        if done is None:
            done = next((o for o in options if already[o] > 0), None)
            if done:
                already[done] -= 1
        if done:
            matched += 1
            track.append((unit, done, "done", options[0]))
            continue
        if unit in full:  # 3 items already: your own choice, not missing
            track.append((unit, options[0], "full", options[0]))
            continue
        blocked = sum((r for u, r in reserved.items() if u != unit), Counter())
        free = spare - blocked
        for api in options:
            if book.is_special(api):  # artifacts can't be built from components
                continue
            need = Counter(book.recipe(api))
            if all(free[c] >= n for c, n in need.items()):
                spare -= need
                slams.append((unit, api))
                track.append((unit, api, "slam", options[0]))
                for r in reserved.values():  # what we just used can't stay reserved
                    r &= spare
                break
        else:
            open_wants.append((idx, unit, options))
            track.append((unit, options[0], "open", options[0]))
            mine = reserved.setdefault(unit, Counter())
            mine |= Counter(book.recipe(options[0])) & free
    # Artifacts / other finished items that aren't in the guide still take a slot: give each to
    # its holder (matched by the unit's other items, else by whose stats it fits) and mark that
    # carry's lowest-priority unfinished item as covered, so it stops being asked for.
    wanted = {a for _, options in wants for a in options}
    for i, items in enumerate(held_units):
        extra = [a for a in items if book.is_built(a) and a not in wanted]
        if not extra:
            continue
        owner = holders.get(i)
        for a in extra:
            unit = owner or stat_fit(a, comp, book)[1]
            mine = [ow for ow in open_wants if ow[1] == unit]
            if not unit or not mine:
                continue
            victim = mine[-1]
            open_wants.remove(victim)
            k = next(i for i, t in enumerate(track) if t[0] == unit and t[1] == victim[2][0] and t[2] == "open")
            track[k] = (unit, a, "covered", victim[2][0])
    return slams, open_wants, spare, matched, track


def synergy_alt(guide_api, held_tags, profile, book, taken=()):
    """An item that fits what the champion already holds better than the guide's pick for this slot
    (Rageblade slammed on a Shojin carry -> an attack-speed item), or None. Only completed,
    craftable items; it has to beat the guide item clearly."""
    def score(a):
        tags = book.tags(a)
        return 2 * len(tags & held_tags) + sum(profile.get(t, 0) for t in tags) / max(sum(profile.values()), 1)
    base = score(guide_api)
    best = max((a for a, it in book.items.items() if it.get("components") and not book.is_special(a)
                and not it["name"].endswith(" Emblem") and a not in taken), key=score, default=None)
    return best if best and score(best) >= base + 2 else None


def fill(comp, bench, held, book):
    """The guide's item slots exactly as the guide lists them — same champions, same order, never
    moved — each as (status, guide item, item you actually hold there, build-around item, the
    slammed item it builds around).
    'Held' fills a slot with your real item, off-guide ones included (a full champion shows its
    3 items). Also returns (carry, slot) to build next, for flashing."""
    held_units, _ = split_held(held)
    track = _state(comp, bench, held, book)[4]
    by_key = {(unit, key): (status, api) for unit, api, status, key in track}
    holders = assign_holders(comp, held_units, book)
    on_unit = {carry: [a for a in held_units[i] if book.is_built(a)] for i, carry in holders.items()}
    profiles = dict(carry_profiles(comp, book))
    out = {}
    for unit, texts in comp["carries"].items():
        slots = []
        for text in texts:
            opts = book.resolve(text)
            if not opts:
                continue
            status, api = by_key.get((unit, opts[0]), (None, None))
            slots.append([status, opts[0], api if status in ("done", "covered") else None, None, None])
        mine = on_unit.get(unit, [])
        if any(sl[0] == "full" for sl in slots):  # 3 items of your own: show them
            rest = [a for a in mine if a not in [sl[2] for sl in slots]]
            for sl in slots:
                if sl[0] == "full" and sl[2] is None and rest:
                    sl[2] = rest.pop(0)
        extra = [a for a in mine if a not in {sl[1] for sl in slots}]
        if extra:  # you slammed something off-guide: open slots should fit it
            held_tags = set().union(*(book.tags(a) for a in extra))
            taken = {sl[1] for sl in slots} | set(mine)
            for sl in slots:
                if sl[0] == "open":
                    sl[3] = synergy_alt(sl[1], held_tags, profiles.get(unit) or Counter(), book, taken)
                    if sl[3]:
                        sl[4] = max(extra, key=lambda a: len(book.tags(a) & book.tags(sl[3])))
                        taken.add(sl[3])
        out[unit] = [tuple(sl) for sl in slots]
    first = next((t for t in track if t[2] == "slam"), None) or next((t for t in track if t[2] == "open"), None)
    nxt = None
    if first:
        keys = [(book.resolve(t) or [None])[0] for t in comp["carries"].get(first[0], [])]
        if first[3] in keys:
            nxt = (first[0], keys.index(first[3]))
    return out, nxt, (first[2] if first else None)


def targets(comp, bench, held, book, max_units=2, per_unit=3, include_done=False):
    """[(unit, [(api, status), ...])] for the top carries that still need something — what the
    icon panel draws. Finished carries (all built, or 3 items of your own) give up their slot."""
    per = {}
    for unit, api, status, _ in _state(comp, bench, held, book)[4]:
        if len(per.setdefault(unit, [])) < per_unit:
            per[unit].append((api, status))
    todo = [(u, lst) for u, lst in per.items() if any(s in ("open", "slam") for _, s in lst)]
    if include_done:  # every carry's build (what to grab if items go), finished ones last
        return (todo + [(u, lst) for u, lst in per.items() if (u, lst) not in todo])[:max_units]
    return (todo or list(per.items()))[:max_units]


def plan(comp, bench, held, book):
    """Returns (lines, n_held_matches): SLAM / HOLD advice for the current bench."""
    slams, open_wants, spare, matched, _ = _state(comp, bench, held, book)
    lines = [f"SLAM {book.name(api)} → {unit}" for unit, api in slams]
    on_unit = {u: Counter(p) for u, p in unit_partials(comp, split_held(held)[0], book).items()}
    for c in sorted(spare.elements(), key=book.name):
        holder = next((u for u, parts in on_unit.items() if parts[c] > 0), None)
        if holder:
            on_unit[holder][c] -= 1
        target = next(((unit, api) for _, unit, options in open_wants for api in options
                       if c in book.recipe(api) and (holder is None or unit == holder)), None)
        if target:
            unit, api = target
            other = [x for x in book.recipe(api) if x != c] or [c]
            where = f"{book.short(c)} on {holder}" if holder else f"HOLD {book.short(c)}"
            lines.append(f"{where} → {book.name(api)} ({unit}), need {book.short(other[0])}")
    return lines, matched


def needs_list(comp, bench, held, book, top_wants=4, why=False):
    """[(component api, count)] still missing for the highest-priority unfinished items, in
    priority order — so the piece that finishes an item already started on a unit comes first,
    even if another component is needed more times. why=True: (component, count, carry it's for)."""
    _, open_wants, spare, _, _ = _state(comp, bench, held, book)
    missing = Counter()
    for _, _, options in open_wants[:top_wants]:
        missing.update(book.recipe(options[0]))
    missing -= spare
    order, have = {}, Counter(spare)
    for _, _, o in open_wants:  # first want (in priority order) that is actually missing it
        for c in book.recipe(o[0]):
            if have[c] > 0:
                have[c] -= 1
            else:
                order.setdefault(c, len(order))
    out = sorted(missing.items(), key=lambda kv: order.get(kv[0], 99))
    if why:
        for_unit = {}
        for _, unit, o in open_wants:
            for c in book.recipe(o[0]):
                for_unit.setdefault(c, unit)
        return [(c, n, for_unit.get(c)) for c, n in out]
    return out


def needs(comp, bench, held, book, top_wants=4):
    """Text form, e.g. 'Rod ×2 · Bow'."""
    return " · ".join(book.short(c) + (f" ×{n}" if n > 1 else "")
                      for c, n in needs_list(comp, bench, held, book, top_wants))


def carry_profiles(comp, book):
    """[(unit, Counter of stat tags over its BIS items)] in priority order."""
    out = {}
    for unit, options in wants_for(comp, book):
        prof = out.setdefault(unit, Counter())
        for t in book.tags(options[0]):
            prof[t] += 1
    return list(out.items())


def stat_fit(label, comp, book):
    """Best carry for an item by stats -> (fit 0..1 minus a small priority penalty, unit, tags)."""
    tags = book.tags(label)
    best = (0.0, None, set())
    for i, (unit, prof) in enumerate(carry_profiles(comp, book)):
        total = sum(prof.values())
        if not total or not tags:
            continue
        fit = sum(prof[t] for t in tags) / total - 0.08 * i
        if fit > best[0]:
            best = (fit, unit, {t for t in tags if prof[t]})
    return best


def rank_picks(offers, comp, bench, held, book, champions=None):
    """Score offered items for this comp. offers: item apis (or raw names for items outside
    the book, e.g. artifacts and emblems). Returns [(label, score, reason, unit)] best first —
    `unit` is the champion the item is for (None if it's for nobody in particular), so the UI
    can show clearly who gets it.

    Order of value: finishes a BIS item now > exact BIS item > component toward a BIS item >
    item whose stats fit a carry > emblem for a trait the comp plays > nothing."""
    before, open_wants, _, _, track = _state(comp, bench, held, book)
    slams_before = {api for _, api in before}
    satisfied = ({api for _, options in wants_for(comp, book) for api in options}
                 - {api for _, _, options in open_wants for api in options})
    # carries that are finished (3 items, or everything built) get nothing more — not even an item
    # that merely fits their stats (no Sunfire "for Hecarim" when Hecarim is full)
    per = {}
    for unit, _, status, _ in track:
        per.setdefault(unit, []).append(status)
    finished = {u for u, sts in per.items() if all(st in ("done", "full", "covered") for st in sts)}
    open_comp = dict(comp, carries={u: v for u, v in comp["carries"].items() if u not in finished})
    # the guide's component priority: how many of each its builds need in total
    need_count = Counter(c for texts in comp["carries"].values() for t in texts
                         for c in book.recipe((book.resolve(t) or [None])[0]) if book.resolve(t))

    def by_stats(label, fallback):
        fit, unit, tags = stat_fit(label, open_comp, book)
        if unit and fit >= 0.3:
            return 30 + round(40 * fit), f"stats fit ({'/'.join(sorted(tags))})", unit
        return (*fallback, None)

    out = []
    for offer in dict.fromkeys(offers):  # unique, keep order
        if offer not in book.items or book.is_special(offer):  # artifact / emblem / special item
            label = book.name(offer) if offer in book.items else offer
            unit = next((u for u, texts in open_comp["carries"].items()
                         for t in texts if label.lower() in t.lower() or t.lower() in label.lower()), None)
            if unit:
                out.append((label, 85, "BIS item", unit))
            elif label.endswith(" Emblem") and champions:
                trait = label[:-len(" Emblem")]
                n = sum(trait in champions[u]["traits"] for u in comp["units"] if u in champions)
                out.append((label, 45 + 3 * n, f"+1 {trait} ({n} in comp)", None) if n >= 2
                           else (label, 8, "trait not in this comp", None))
            else:
                out.append((label, *by_stats(label, (5, "no fit for this comp"))))
            continue
        name = book.name(offer)
        if book.recipe(offer):  # completed item
            hit = next(((i, u) for i, u, options in open_wants if offer in options), None)
            if hit:
                i, unit = hit
                out.append((name, 100 - 6 * i, "BIS item", unit))
            elif offer in satisfied:
                out.append((name, 15, "already built", None))
            else:
                out.append((name, *by_stats(offer, (5, "not in this comp"))))
            continue
        # component: does it finish something right now?
        new = [(u, a) for u, a in _state(comp, list(bench) + [offer], held, book)[0]
               if a not in slams_before]
        if new:
            unit, api = new[0]
            i = next((i for i, u, options in open_wants if api in options), 0)
            out.append((name, 95 - 6 * i + min(12, 2 * need_count[offer]), f"completes {book.name(api)}", unit))
            continue
        uses = [(i, unit, api) for i, unit, options in open_wants for api in options[:1]
                if offer in book.recipe(api)]
        if uses:
            i, unit, api = uses[0]
            out.append((name, 60 - 5 * i + 4 * (len(uses) - 1) + min(12, 2 * need_count[offer]),
                        f"→ {book.name(api)}", unit))
        else:
            out.append((name, 8, "no use in this comp", None))
    out.sort(key=lambda r: -r[1])
    return out
