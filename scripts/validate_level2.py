"""Check three Level 2 CSVs on disk against the flow file they describe, without rerunning any agent.

Reads no labels. Exit status 1 means do not submit.

Usage: .venv/bin/python scripts/validate_level2.py --input PATH/TO/hidden.parquet --out-dir data/processed/level2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.level2 import validate_submission  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--expiry-boundary", choices=["inclusive", "exclusive"],
                    help="Defaults to level2.defense.expiry_boundary.")
    args = ap.parse_args()
    problems = validate_submission(args.out_dir, args.input, args.expiry_boundary)
    if problems:
        print("\n".join(problems[:50]))
        raise SystemExit(f"{len(problems)} rule violations; do not submit these files")
    print("rule check: passed")


if __name__ == "__main__":
    main()
