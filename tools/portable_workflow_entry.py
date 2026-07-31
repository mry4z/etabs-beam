"""PyInstaller entry point for the portable ETABS Beam Generator workflow."""

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
