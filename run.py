"""Default: the live overlay (reads your screen; personal use — outside Riot's third-party policy).
--guide: static build guide that never reads the screen."""
import sys

if __name__ == "__main__":
    if "--guide" in sys.argv:
        sys.argv.remove("--guide")
        from tft_overlay.guide_app import main
    else:
        sys.argv = [a for a in sys.argv if a != "--live"]  # old flag, now the default
        from tft_overlay.app import main
    main()
