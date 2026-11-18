#!/usr/bin/env python3
"""Launcher: python3 tw.py <command>. Works from any directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tw.cli import main  # noqa: E402

sys.exit(main())
