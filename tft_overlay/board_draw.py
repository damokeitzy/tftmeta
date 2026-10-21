"""Draws a comp's exact board on a Tk canvas: 4 rows x 7 hexes (teamfight.lol positions 1-28,
rows 2 and 4 shifted half a hex right), champion portrait in each hex, 3★ stars above, items
across the bottom of the portrait (drawn last, so the next row's hexes can't cover them). Used by the live overlay's max mode and the guide."""
import math

from .ui_common import ACCENT, CARD, COST_COLOR, DIM, FG, GREEN

ROWS, COLS = 4, 7


def board_height(width):
    """Canvas height the board needs at a given width."""
    r = width / (COLS + 0.5) / math.sqrt(3)  # hex radius from the column pitch
    return int(r * 1.5 * (ROWS - 1) + 2 * r + r * 0.9)


def draw_hex_board(c, x0, y0, width, board, icons, champs, item_icon, shop=(), font=("Segoe UI",), later=(),
                   item_fill=None, item_next=None):
    """board: [{"unit", "pos", "three", "items", "prop", "icon"}]; champs: set data champions
    (for cost colours); item_icon(name) -> icon file or None; shop: unit names in your shop
    (flagged BUY, flashing); later: units for later (4/5-costs before stage 4), dimmed so the
    rest reads as the board to play now; item_fill: {unit: [(status, icon of the item you really
    hold there or None)]} — the guide's slots never move: an item you hold is drawn bright with a
    tick (your real item, even off-guide), one you still need is dimmed, and item_next =
    [unit, slot] flashes (build this next). Returns the bottom y."""
    live = item_fill is not None  # live overlay: hexes show only what your champions hold
    item_fill = item_fill or {}
    pitch = width / (COLS + 0.5)
    r = pitch / math.sqrt(3)
    fam = font[0]
    by_pos = {b["pos"]: b for b in board}
    item_rows = []  # (cx, cy, items): drawn after every hex
    for row in range(ROWS):
        for col in range(COLS):
            cx = x0 + pitch / 2 + col * pitch + (pitch / 2 if row % 2 else 0)
            cy = y0 + r + row * r * 1.5
            pts = []
            for k in range(6):  # pointy-top hexagon
                a = math.radians(60 * k - 90)
                pts += [cx + r * 0.94 * math.cos(a), cy + r * 0.94 * math.sin(a)]
            b = by_pos.get(row * COLS + col + 1)
            if not b:
                c.create_polygon(pts, fill="", outline="#3a3f4b", width=1)
                continue
            if b["prop"]:
                c.create_polygon(pts, fill="#2a1414", outline="#e05a3a", width=2)
                c.create_text(cx, cy, text=b["unit"], fill=FG, width=pitch * 0.9, justify="center",
                              font=(fam, max(6, int(r * 0.22)), "bold"))
                continue
            unit = b["unit"]
            cost = champs.get(unit, {}).get("cost", 1)
            carry = bool(b["items"])
            in_shop = unit in shop
            ring = GREEN if in_shop else COST_COLOR.get(cost, DIM)
            c.create_polygon(pts, fill=CARD, outline=ring, width=max(4, int(r * 0.14)) if in_shop else max(2, int(r * 0.08)),
                             tags=("flashring",) if in_shop else ())
            size = int(r * 1.15)
            img = icons.get(b.get("icon"), size, folder="champ_icons")
            if img:
                c.create_image(cx, cy, image=img)
            # name at the bottom; on itemised units the items take the bottom, so the name goes up top.
            # In your shop: the name strip says BUY and flashes with the outline; the face stays visible.
            name_y = cy - r * 0.5 if carry else cy + r * 0.62
            t = c.create_text(cx, name_y, text=f"BUY {unit}" if in_shop else unit, fill="#111" if in_shop else FG,
                              font=(fam, max(6, int(r * 0.2)), "bold"))
            x1, y1, x2, y2 = c.bbox(t)  # strip behind the name: readable on light portraits
            c.tag_lower(c.create_rectangle(x1 - 2, y1, x2 + 2, y2, fill=GREEN if in_shop else "#111318", outline="",
                                           tags=("flashbadge",) if in_shop else ()), t)
            if b["unit"] in later and not in_shop:  # dimmed: comes in at stage 4
                c.create_polygon(pts, fill="#000000", stipple="gray50", outline="")
                c.create_text(cx, cy, text="later", fill=DIM, font=(fam, max(6, int(r * 0.22)), "bold"))
            if b["three"]:
                c.create_text(cx, cy - r * 0.84, text="★★★", fill=ACCENT, font=(fam, max(6, int(r * 0.22)), "bold"))
            if carry:
                item_rows.append((cx, cy, b["items"], b["unit"]))
    # items last: a row across the bottom of the portrait, inside the hex
    isz = int(r * 0.46)
    for cx, cy, items, unit in item_rows:
        fills = item_fill.get(unit) or []
        if live:  # only the items this champion really has (the guide's build is in the panel below)
            items = [have for _, have in fills if have]
            fills = [(None, have) for have in items]
        ix, iy = cx - len(items) * isz / 2, cy + r * 0.36
        for k, name in enumerate(items):
            st, have = fills[k] if k < len(fills) else (None, None)
            nxt = bool(item_next) and item_next[0] == unit and item_next[1] == k
            iimg = icons.get(have or item_icon(name), isz)
            if iimg:
                c.create_image(ix, iy, image=iimg, anchor="nw")
            else:
                c.create_rectangle(ix, iy, ix + isz, iy + isz, fill=CARD, outline=DIM)
            if fills and not have and not nxt:  # still to get: dimmed
                c.create_rectangle(ix, iy, ix + isz, iy + isz, fill="#000000", stipple="gray50", outline="")
            if have:  # yours
                c.create_text(ix + isz - 1, iy + isz - 1, text="✓", anchor="se", fill=GREEN,
                              font=(fam, max(7, int(isz * 0.45)), "bold"))
            c.create_rectangle(ix, iy, ix + isz, iy + isz, outline=ACCENT if nxt else "#000000",
                               width=max(2, int(isz * 0.12)) if nxt else 1, tags=("flashring",) if nxt else ())
            ix += isz
    return y0 + board_height(width)
