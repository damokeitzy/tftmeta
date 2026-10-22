"""Grabs the game window's pixels into memory (no disk writes, no game-process access).

The window is found by title only — we never open a handle to the game process,
which is the kind of thing anti-cheat watches for.
"""
import ctypes
from ctypes import wintypes

import mss
import numpy as np

# Standalone TFT client (title is "TFT  " with trailing spaces), then the classic League game window.
GAME_WINDOW_TITLES = ("TFT", "League of Legends (TM) Client")

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixel coords on scaled displays
except Exception:
    user32.SetProcessDPIAware()


def find_game_window():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() in GAME_WINDOW_TITLES:
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found[0] if found else None


def game_rect():
    """Screen rect of the game's client area, or None if the game isn't open/visible."""
    hwnd = find_game_window()
    if not hwnd or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 200 or h < 200:
        return None
    return {"left": pt.x, "top": pt.y, "width": w, "height": h}


class Capturer:
    def __init__(self):
        self.sct = mss.MSS()

    def grab(self, region=None):
        """RGB numpy frame of the game (or just `region`, fractions x0,y0,x1,y1), or None."""
        rect = game_rect()
        if rect is None:
            return None
        if region:
            x0, y0, x1, y1 = region
            rect = {"left": rect["left"] + int(rect["width"] * x0), "top": rect["top"] + int(rect["height"] * y0),
                    "width": max(1, int(rect["width"] * (x1 - x0))),
                    "height": max(1, int(rect["height"] * (y1 - y0)))}
        shot = self.sct.grab(rect)
        return np.asarray(shot)[:, :, 2::-1]  # BGRA -> RGB
