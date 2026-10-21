"""Shared UI bits: config, colors, icon loading. No screen capture in here, so the
compliant guide can use it without pulling in any of the live-reading code."""
import ctypes
import json
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageEnhance, ImageTk

from .gamedata import DATA

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
DEFAULTS = {"interval_s": 2.5, "x": 1575, "y": 420, "width": 335, "height": 600, "alpha": 0.9,
            "locked": None, "save_carousel_frames": True,
            "player_name": ""}  # your in-game name: the scout never counts that board as an opponent

BG, CARD, FG, DIM, ACCENT, GREEN = "#14161c", "#1d2029", "#e8e6e3", "#8b8f98", "#e0b44c", "#7fe08a"
TIER_COLOR = {"S": "#ff7f7f", "A": "#ffbf7f", "B": "#ffdf7f", "C": "#bfff7f", "D": "#9aa0a6",
              "Special": "#7fbfff"}
COST_COLOR = {1: "#9aa0a6", 2: "#3fbf6f", 3: "#3f8fdf", 4: "#b04fe0", 5: "#e0b44c"}
TIER_ORDER = ["S", "A", "B", "C", "D", "Special"]


def tier_rank(comp):
    """Sort key: tier, then real average placement (lower is better) when known."""
    t = comp.get("tier")
    return (TIER_ORDER.index(t) if t in TIER_ORDER else len(TIER_ORDER),
            (comp.get("stats") or {}).get("avg", 9))


def load_config():
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text(encoding="utf-8-sig"))}  # Notepad may add a BOM
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=1))


def clamp_to_screen(x, y, w, h):
    """Keep a window inside the *virtual* desktop (all monitors), not just the primary one —
    Tk's winfo_screenwidth only knows the primary monitor."""
    try:
        import ctypes
        gsm = ctypes.windll.user32.GetSystemMetrics
        vx, vy, vw, vh = gsm(76), gsm(77), gsm(78), gsm(79)  # SM_[XY]VIRTUALSCREEN, SM_C[XY]VIRTUALSCREEN
    except Exception:
        return x, y
    return min(max(x, vx), vx + vw - w), min(max(y, vy), vy + vh - h)


def mix(c1, c2, t):
    """Blend two #rrggbb colors, t in 0..1."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class IconCache:
    """PhotoImages by (folder, file, size, dim). Tk drops images that aren't referenced,
    so they all live here."""

    def __init__(self):
        self.images = {}

    def get(self, file, size, dim=False, folder="icons"):
        key = (folder, file, size, dim)
        if key not in self.images:
            path = DATA / folder / file if file else None
            if not path or not path.exists():
                return None
            img = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
            if dim:
                img = ImageEnhance.Brightness(ImageEnhance.Color(img).enhance(0.2)).enhance(0.45)
            self.images[key] = ImageTk.PhotoImage(img)
        return self.images[key]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitor_rect(x, y):
    """(left, top, width, height) of the monitor containing screen point (x, y)."""
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(MONITORINFO))
    hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(hmon, ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top
