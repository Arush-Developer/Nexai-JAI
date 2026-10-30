"""Run all five agents over a flow file in the supplied order and write the three Level 2 CSVs.

Nothing is uploaded: the organisers' Level 2 API details are not yet available. The file's labels, if
it has any, are ignored. Checks the files on disk against the brief's rules, reruns replay on prefixes
of the stream to prove no future record changed a decision, and refuses to report success otherwise.

Usage: .venv/bin/python scripts/run_level2.py --input PATH/TO/hidden.parquet --out-dir data/processed/level2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from backend.preprocessing.data_processor import load_flows  # noqa: E402
from backend.services.level2 import (  # noqa: E402
    check_prefix_invariance, csv_schema, run_level2, validate_submission, write_outputs)
from backend.services.model_service import Pipeline  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/processed/level2")
    ap.add_argument("--on-order-conflict", choices=["error", "report"],
                    help="Override level2.replay_order.on_conflict for this run.")
    args = ap.parse_args()
    partition = load_flows(args.input)
    result = run_level2(Pipeline.load(), partition, on_order_conflict=args.on_order_conflict)
    problems = check_prefix_invariance(result["scored"], result)
    paths = write_outputs(result, args.out_dir)
    problems += validate_submission(args.out_dir, args.input, result["expiry_boundary"])
    print(f"records={len(result['replay'])} policies={len(result['policy'])} groups={len(result['groups'])} "
          f"blocked={(result['replay']['action'] == 'BLOCK').sum()} expiry_boundary={result['expiry_boundary']}")
    print(f"order: {result['order_report']}")
    for name, path in paths.items():
        print(f"wrote {name}: {path}")
    if not load_config().get("level2", {}).get("replay_schema_confirmed", False):
        print(f"WARNING: replay.csv header {list(csv_schema()['replay'].values())} is not confirmed by the organisers. "
              "Set level2.columns.replay and level2.replay_schema_confirmed in configs/default.yaml.")
    if result["order_report"]["ambiguities"]:
        print("WARNING: replay order ambiguities, resolve with the organisers before submitting:")
        print("\n".join(result["order_report"]["ambiguities"]))
    if problems:
        print("\n".join(problems[:50]))
        raise SystemExit(f"{len(problems)} rule violations; do not submit these files")
    print("rule check: passed (files read back from disk; prefix replay unchanged)")


if __name__ == "__main__":
    main()
