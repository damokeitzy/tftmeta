"""Where the bottom HUD bar is: shop cards, level, XP, shop odds, gold, streak.

The October 2026 client update (18.4b) moved the whole bar 5% of the window width to the left (96 px at
1920 wide). The regions in shoplog.py and econ.py are written for the old layout; shift() moves them by
the layout in use. Which one that is gets worked out from the shop: whichever layout reads more champion
names on the five cards (ShopLogger.names), so an older client, or Riot moving it back, still works.
"""
LAYOUTS = (-0.05, 0.0)  # x offset (fraction of the window width): the new client first, then the old one
_dx = [LAYOUTS[0]]


def dx():
    return _dx[0]


def set_dx(value):
    if value != _dx[0]:
        print(f"HUD layout: bottom bar offset {value:+.2f}", flush=True)
    _dx[0] = value


def shift(region, d=None):
    """An old-layout region (x0, y0, x1, y1 fractions) where it is in the layout in use (or offset d)."""
    d = dx() if d is None else d
    x0, y0, x1, y1 = region
    return (x0 + d, y0, x1 + d, y1)
