"""PyInstaller entry point for the portable ETABS Beam Generator workflow."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
