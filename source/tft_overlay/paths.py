"""Where the app's files live, whether it runs from source (py run.py) or as TFTMeta.exe.

From source: the source folder. As the exe: the folder with a data/ folder next to the exe, or the
source folder when the exe sits in source\\dist\\TFTMeta\\ (so your own build uses your existing builds,
history and settings). A brand-new folder gets the starter files (your builds, overrides) copied in
on first start; the game data and guides download at startup as usual.
"""
import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))  # files packed into the exe
SEED = ["comp_overrides.json", "custom_comps.json", "shop_odds.json", "special.json", "item_stats.json",
        "teamfight_links.txt"]  # data files that ship with the app (BUNDLE/seed)


def _root():
    if os.environ.get("TFTMETA_ROOT"):  # a tool started by the exe: same folder as the app
        return Path(os.environ["TFTMETA_ROOT"])
    if not FROZEN:
        return Path(__file__).resolve().parent.parent
    exe = Path(sys.executable).resolve().parent
    for folder in (exe, exe.parent, exe.parent.parent):  # dist\TFTMeta\TFTMeta.exe inside the project
        if (folder / "data" / "comp_overrides.json").exists() and (folder / "tft_overlay").exists():
            return folder
    return exe


ROOT = _root()
DATA = ROOT / "data"


def ensure_data():
    """First start of the exe in a new folder: data/ with the starter files, and the folders it saves to."""
    DATA.mkdir(exist_ok=True)
    for name in SEED:
        src = BUNDLE / "seed" / name
        if src.exists() and not (DATA / name).exists():
            shutil.copy2(src, DATA / name)
    (ROOT / "tests" / "fixtures" / "reports").mkdir(parents=True, exist_ok=True)
