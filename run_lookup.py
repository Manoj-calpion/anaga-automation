"""Run from the project root: python run_lookup.py [--spike] [args...]"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from main import cli

if __name__ == "__main__":
    cli()
