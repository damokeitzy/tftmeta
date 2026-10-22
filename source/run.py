"""Default: the live overlay (reads your screen; personal use — outside Riot's third-party policy).
--guide: static build guide that never reads the screen.

As TFTMeta.exe the app also runs its own data tools (the startup syncs start `TFTMeta.exe tools\\x.py --sync`
the way the source version starts `python tools\\x.py --sync`)."""
import os
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):  # one of the data tools, by file name
        import runpy
        from pathlib import Path

        from tft_overlay.paths import ROOT
        os.environ["TFTMETA_ROOT"] = str(ROOT)  # the tool reads and writes the app's data folder
        tool = Path(sys.argv[1]).stem
        sys.argv = sys.argv[1:]
        try:
            runpy.run_module(f"tools.{tool}", run_name="__main__", alter_sys=True)
        except SystemExit:
            raise
        except BaseException:  # e.g. a site refusing the request: report it to the app, never a popup
            import traceback
            if sys.stderr:
                traceback.print_exc()
            sys.exit(1)
        sys.exit(0)
    from tft_overlay.paths import FROZEN, ensure_data
    if FROZEN:
        ensure_data()
    if "--guide" in sys.argv:
        sys.argv.remove("--guide")
        from tft_overlay.guide_app import main
    else:
        sys.argv = [a for a in sys.argv if a != "--live"]  # old flag, now the default
        from tft_overlay.app import main
    main()
