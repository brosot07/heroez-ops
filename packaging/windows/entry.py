"""PyInstaller entry point for HeroezOps.exe."""
import sys

from heroez_ops.winmain import main

if __name__ == "__main__":
    sys.exit(main())
