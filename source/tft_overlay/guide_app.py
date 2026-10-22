"""Compliant mode: an always-on-top static guide for the build you pick.

Never captures or reads the screen. Everything shown is a fixed function of the chosen comp
(see guide.py), which is what Riot's policy allows: static recommendations, not suggestions
that react to your game state.

Two sizes: the compact overlay (fixed size, drag anywhere) and maximised — fills the monitor the
window is on (e.g. your second screen) with a larger two-column layout. ⤢ or double-click the
title bar toggles.
"""
import json
import math
import tkinter as tk

from .gamedata import DATA, load_comps, load_set
from .guide import build_guide
from .slams import ItemBook
from .ui_common import (ACCENT, BG, CARD, COST_COLOR, DIM, FG, GREEN, TIER_COLOR, IconCache,
                        clamp_to_screen, load_config, mix, monitor_rect, save_config, tier_rank)


class GuideOverlay:
    def __init__(self):
        self.cfg = load_config()
        self.comps = sorted(load_comps(), key=tier_rank)
        self.by_name = {c["name"]: c for c in self.comps}
        self.set_data = load_set()
        self.book = ItemBook(json.loads((DATA / "items.json").read_text(encoding="utf8")))
        self.icons = IconCache()
        self.glows, self.t = [], 0.0
        self.s = 1.0          # layout scale: 1 compact, larger when maximised
        self.maximized = False
        self.current = None   # comp name shown, or None for the picker

        root = self.root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.cfg["alpha"])
        root.configure(bg=BG)

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=8, pady=(6, 2))
        title = tk.Label(bar, text="TFT Guide", fg=ACCENT, bg=BG, font=("Segoe UI", 10, "bold"))
        title.pack(side="left")
        btn = dict(fg=DIM, bg=BG, activebackground=BG, activeforeground=FG, bd=0,
                   font=("Segoe UI", 9), cursor="hand2")
        tk.Button(bar, text="✕", command=root.destroy, **btn).pack(side="right")
        self.max_btn = tk.Button(bar, text="⤢", command=self.toggle_max, **btn)
        self.max_btn.pack(side="right", padx=(0, 2))
        self.build_btn = tk.Menubutton(bar, width=20, anchor="e", relief="flat", bg=BG, fg=FG,
                                       activebackground=CARD, activeforeground=FG,
                                       font=("Segoe UI", 8), cursor="hand2")
        menu = tk.Menu(self.build_btn, tearoff=0, bg=CARD, fg=FG, activebackground=ACCENT,
                       activeforeground="#111", font=("Segoe UI", 9))
        for comp in self.comps:
            menu.add_command(label=f"{comp['tier']:<7} {comp['name']}",
                             command=lambda n=comp["name"]: self.pick(n))
        menu.add_separator()
        menu.add_command(label="Clear build", command=lambda: self.pick(None))
        self.build_btn["menu"] = menu
        self.build_btn.pack(side="right", padx=4)
        for wdg in (bar, title):
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag)
            wdg.bind("<ButtonRelease-1>", self._drag_end)
            wdg.bind("<Double-Button-1>", lambda _e: self.toggle_max())

        tk.Label(root, text="Static guide · doesn't read your screen", fg=DIM, bg=BG,
                 font=("Segoe UI", 7), anchor="w").pack(side="bottom", fill="x", padx=8, pady=(0, 5))
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True, padx=8)

        if self.cfg.get("maximized") and self.cfg.get("max_at"):
            self._maximize(*self.cfg["max_at"], save=False)
        else:
            self._restore(save=False)
        self.pick(self.cfg.get("locked") if self.cfg.get("locked") in self.by_name else None, save=False)

    # ---- window plumbing -------------------------------------------------------------------
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        if not self.maximized:
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, _):
        if not self.maximized:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
            save_config(self.cfg)

    def toggle_max(self):
        if self.maximized:
            self._restore()
        else:  # fill the monitor the compact window is on
            x = self.root.winfo_x() + self.root.winfo_width() // 2
            y = self.root.winfo_y() + self.root.winfo_height() // 2
            self._maximize(x, y)
        self.redraw()

    def _maximize(self, x, y, save=True):
        left, top, w, h = monitor_rect(x, y)
        self.maximized = True
        self.root.geometry(f"{w}x{h}+{left}+{top}")
        self._max_size = (w - 16, h - 60)  # canvas area; Tk reports the new size too late to use
        self.root.attributes("-alpha", 1.0)  # full screen on its own monitor: no need to see through
        self.max_btn.config(text="⤡")
        if save:
            self.cfg["maximized"], self.cfg["max_at"] = True, [x, y]
            save_config(self.cfg)

    def _restore(self, save=True):
        self.maximized = False
        w, h = self.cfg["width"], self.cfg["height"]
        x, y = clamp_to_screen(self.cfg["x"], self.cfg["y"], w, h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.attributes("-alpha", self.cfg["alpha"])
        self.max_btn.config(text="⤢")
        if save:
            self.cfg["maximized"] = False
            save_config(self.cfg)

    def pick(self, name, save=True):
        self.current = name
        self.cfg["locked"] = name
        if save:
            save_config(self.cfg)
        self.build_btn.config(text=(name or "Pick your build") + " ▾", fg=ACCENT if name else FG)
        self.redraw()

    def redraw(self):
        self.root.update_idletasks()
        if self.maximized:
            W, H = self._max_size
        else:
            W, H = self.cfg["width"] - 16, self.cfg["height"] - 60
        if self.maximized:
            self.cols = 2
            colw = (W - 40) / 2
            self.s = max(1.0, min(colw / 330, H / 430))
        else:
            self.cols, self.s = 1, 1.0
        self.c.delete("all")
        self.glows = []
        if self.current:
            self.draw_guide(build_guide(self.by_name[self.current], self.book, self.set_data, self.comps), W)
        else:
            self.draw_picker(W)

    # ---- scaled drawing helpers ------------------------------------------------------------
    def P(self, v):
        return int(round(v * self.s))

    def F(self, size, *style):
        return ("Segoe UI", max(6, int(round(size * self.s))), *style)

    def _section(self, x0, y, W, text):
        tid = self.c.create_text(x0, y, text=text, anchor="nw", fill=ACCENT, font=self.F(8, "bold"))
        self.c.create_line(self.c.bbox(tid)[2] + self.P(6), y + self.P(7), x0 + W, y + self.P(7), fill=CARD)
        return y + self.P(18)

    def _icon(self, x, y, size, file, label, folder="icons", ring=None, width=1):
        img = self.icons.get(file, size, folder=folder)
        if img:
            self.c.create_image(x, y, image=img, anchor="nw")
        else:
            self.c.create_rectangle(x, y, x + size, y + size, fill=CARD, outline="")
            self.c.create_text(x + size / 2, y + size / 2, text=label[:5], fill=FG, font=self.F(6))
        return self.c.create_rectangle(x - 1, y - 1, x + size + 1, y + size + 1,
                                       outline=ring or "#000000", width=self.P(width))

    def _text(self, x0, y, W, text, color, size):
        if not text:
            return y
        tid = self.c.create_text(x0, y, text=text, anchor="nw", fill=color, width=W, font=self.F(size))
        return self.c.bbox(tid)[3]

    def _link(self, x, y, name):
        c, tag = self.c, f"link:{name}:{x}:{y}"
        tid = c.create_text(x, y, text=name, anchor="nw", fill=ACCENT, font=self.F(7, "underline"), tags=tag)
        c.tag_bind(tag, "<Button-1>", lambda _e, n=name: self.pick(n))
        c.tag_bind(tag, "<Enter>", lambda _e: c.config(cursor="hand2"))
        c.tag_bind(tag, "<Leave>", lambda _e: c.config(cursor=""))
        return c.bbox(tid)[2]

    # ---- views -----------------------------------------------------------------------------
    def draw_picker(self, W):
        c, P = self.c, self.P
        self.s = min(self.s, 1.6)
        c.create_text(0, P(4), text="Pick your build before the game", anchor="nw", fill=FG, font=self.F(10, "bold"))
        c.create_text(0, P(26), text="The guide stays fixed all game — it never reads the screen.",
                      anchor="nw", fill=DIM, font=self.F(8))
        y, x0 = P(50), 0
        for comp in self.comps:
            tag = f"pick:{comp['name']}"
            c.create_rectangle(x0, y, x0 + P(26), y + P(18), fill=TIER_COLOR.get(comp["tier"], DIM),
                               outline="", tags=tag)
            c.create_text(x0 + P(13), y + P(9), text=comp["tier"][:3], fill="#111", font=self.F(7, "bold"), tags=tag)
            c.create_text(x0 + P(34), y + P(9), text=comp["name"], anchor="w", fill=FG, font=self.F(9), tags=tag)
            c.tag_bind(tag, "<Button-1>", lambda _e, n=comp["name"]: self.pick(n))
            c.tag_bind(tag, "<Enter>", lambda _e: c.config(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda _e: c.config(cursor=""))
            y += P(22)
            if self.maximized and y > c.winfo_height() - P(30):  # wrap into another column
                y, x0 = P(50), x0 + P(300)

    def draw_guide(self, g, W):
        c, P = self.c, self.P
        c.create_rectangle(0, P(2), P(30), P(20), fill=TIER_COLOR.get(g["tier"], DIM), outline="")
        c.create_text(P(15), P(11), text=g["tier"][:3], fill="#111", font=self.F(8, "bold"))
        c.create_text(P(38), P(11), text=g["name"], anchor="w", fill=FG, font=self.F(11, "bold"))
        top = P(28)
        if self.cols == 2:
            colw = (W - 40) / 2
            y = self.sec_plan(g, 0, top, colw)
            y = self.sec_board(g, 0, y, colw)
            self.sec_carries(g, 0, y, colw)
            x1 = colw + 40
            y = self.sec_components(g, x1, top, colw)
            self.sec_notes(g, x1, y, colw)
        else:
            y = self.sec_plan(g, 0, top, W)
            y = self.sec_board(g, 0, y, W)
            y = self.sec_carries(g, 0, y, W)
            y = self.sec_components(g, 0, y, W)
            self.sec_notes(g, 0, y, W)

    def sec_plan(self, g, x0, y, W):
        """How to play it: roll level, timing, realistic target, when to bail, where to go."""
        plan = g.get("plan")
        if not plan:
            return y
        c, P = self.c, self.P
        y = self._section(x0, y, W, f"GAME PLAN  ·  {plan['style']}")
        c.create_rectangle(x0, y, x0 + P(70), y + P(18), fill=ACCENT, outline="")
        c.create_text(x0 + P(35), y + P(9), text=f"Roll at {plan['roll_at'].replace('Level ', 'lv ')}",
                      fill="#111", font=self.F(7, "bold"))
        tid = c.create_text(x0 + P(76), y, text=plan["when"], anchor="nw", fill=FG, width=W - P(76),
                            font=self.F(7))
        y = max(y + P(20), c.bbox(tid)[3]) + P(3)
        if plan.get("realistic"):
            y = self._text(x0, y, W, plan["realistic"], GREEN, 7) + P(3)
        y = self._text(x0, y, W, plan["bail"], DIM, 7) + P(2)
        if g["pivots"]:
            x = c.bbox(c.create_text(x0, y, text="Switch to:", anchor="nw", fill=DIM, font=self.F(7)))[2] + P(6)
            for name in g["pivots"]:
                x = self._link(x, y, name) + P(10)
            y += P(14)
        return y + P(4)

    def sec_board(self, g, x0, y, W):
        """Units in fielding order: carries (gold) first; dashed marker after unit 7 = level 7 board."""
        c, P = self.c, self.P
        y = self._section(x0, y, W, "BOARD  (gold = core · ┆ = level 7 board)")
        size, step = P(28), P(33)
        for i, (unit, icon, cost, carry) in enumerate(g["units"][:9]):
            x = x0 + i * step + (P(8) if i >= 7 else 0)
            self._icon(x, y, size, icon, unit, folder="champ_icons",
                       ring=ACCENT if carry else COST_COLOR.get(cost, DIM), width=2 if carry else 1)
            if self.maximized:  # room for names under the portraits
                c.create_text(x + size / 2, y + size + P(6), text=unit, fill=DIM, font=self.F(5))
        if len(g["units"]) > 7:
            mx = x0 + 7 * step + P(1)
            c.create_line(mx, y - P(2), mx, y + size + P(2), fill=ACCENT, dash=(2, 2))
        y += size + P(14 if self.maximized else 4)
        traits = "  ".join(f"{t} {bp}" for t, n, bp in g["traits"] if n >= 2)
        return self._text(x0, y, W, traits, DIM, 7) + P(4)

    def sec_carries(self, g, x0, y, W):
        """Carries and their BIS items, main carry first."""
        c, P = self.c, self.P
        y = self._section(x0, y, W, "CARRY ITEMS  (priority order)")
        for unit, icon, items in g["carries"][:3]:
            self._icon(x0, y, P(30), icon, unit, folder="champ_icons", ring=ACCENT)
            c.create_text(x0 + P(36), y + P(15), text=unit, anchor="w", fill=FG, font=self.F(8, "bold"))
            x = x0 + P(112)
            for file, label in items[:3]:
                if file:
                    self._icon(x, y + P(1), P(28), file, label)
                    if self.maximized:
                        c.create_text(x + P(14), y + P(33), text=label, fill=DIM, font=self.F(5))
                    x += P(34)
                else:  # e.g. "Leftover AP" – no icon, show the text
                    tid = c.create_text(x, y + P(15), text=label, anchor="w", fill=DIM, font=self.F(7),
                                        width=x0 + W - x)
                    x = c.bbox(tid)[2] + P(8)
            alts = [label for file, label in items if file and "/" in label]
            if alts:
                c.create_text(x0 + W, y + P(15), text="alt: " + alts[0].split("/", 1)[1].strip(), anchor="e",
                              fill=DIM, font=self.F(6))
            y += P(42 if self.maximized else 36)
        return y

    def sec_components(self, g, x0, y, W):
        """What to grab from carousels / drops for this build; #1 pulses."""
        c, P = self.c, self.P
        y = self._section(x0, y + P(2), W, "COMPONENT PRIORITY  (carousel & drops)")
        for k, (file, name, n) in enumerate(g["components"][:7]):
            x = x0 + k * P(45)
            ring = self._icon(x, y, P(28), file, name, ring=ACCENT if k == 0 else None, width=2 if k == 0 else 1)
            if k == 0:
                self.glows.append(ring)
            if n > 1:
                c.create_rectangle(x + P(16), y + P(17), x + P(30), y + P(30), fill="#000000", outline="")
                c.create_text(x + P(23), y + P(24), text=f"×{n}", fill=FG, font=self.F(7, "bold"))
            c.create_text(x + P(14), y + P(38), text=name, fill=DIM, font=self.F(7))
        return y + P(50)

    def sec_notes(self, g, x0, y, W):
        P = self.P
        y = self._section(x0, y, W, "AUGMENTS")
        augs = g["augments"] or ["No augment notes for this build yet — add them under \"augments\" "
                                 "in data/comps.json."]
        y = self._text(x0, y, W, "  ·  ".join(augs), FG if g["augments"] else DIM, 8) + P(2)
        if g["notes"]:
            y = self._section(x0, y + P(4), W, "NOTES")
            y = self._text(x0, y, W, g["notes"], FG, 8)
        return y

    def animate(self):
        """Gentle pulse on the #1 component — the only thing that moves."""
        if self.glows:
            self.t += 0.05
            pulse = (math.sin(self.t * 5) + 1) / 2
            for ring in self.glows:
                self.c.itemconfigure(ring, outline=mix("#5a4a20", ACCENT, pulse), width=self.P(2 + round(pulse)))
        self.root.after(50, self.animate)

    def run(self):
        self.animate()
        self.root.mainloop()


def main():
    GuideOverlay().run()
