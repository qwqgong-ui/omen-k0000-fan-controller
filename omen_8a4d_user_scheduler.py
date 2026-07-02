#!/usr/bin/env python3
"""Compatibility launcher for source-tree runs."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from omen_8a4d_user_scheduler.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
