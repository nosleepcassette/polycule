# Polycule · MIT
"""Console-script shim for the legacy ``bin/polycule`` CLI."""

from __future__ import annotations

import runpy
from pathlib import Path


def main():
    cli_path = Path(__file__).resolve().parents[1] / "bin" / "polycule"
    runpy.run_path(str(cli_path), run_name="__main__")
