"""Build a SYNTHETIC hidden-style input for testing the Data Manager, because the real hidden set is not released.

The flow features are real official eval records. The endpoints and timestamps are INVENTED, from documentation
address ranges, so this proves the mechanism and nothing about hidden-set performance. Addresses come from each
flow's Destination Port feature, never from its label, and the output carries no label column.

Writes hidden_style_synthetic.csv and a ZIP of it, into --out-dir (default data/processed/local_fixture).
Usage: .venv/bin/python scripts/make_local_fixture.py --rows 20000
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.config import load_config, resolve  # noqa: E402

SECONDS_PER_RECORD = 0.5


def build(rows: int) -> pd.DataFrame:
    data = load_config()["data"]
    raw = pd.read_parquet(resolve(data["eval_parquet"])).drop(columns=[data["label_column"]]).head(rows)
    ports = raw["Destination Port"].astype(int).to_numpy()
    return raw.assign(
        source_ip=[f"198.51.100.{port % 200 + 1}" for port in ports],
        destination_ip=[f"192.0.2.{port % 40 + 1}" for port in ports],
        source_port=[49152 + i % 16000 for i in range(len(raw))],
        destination_port=ports,
        protocol="TCP",
        timestamp=(pd.Timestamp("2026-01-01T00:00:00Z")
                   + pd.to_timedelta([i * SECONDS_PER_RECORD for i in range(len(raw))], unit="s")).astype(str),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=20000)
    ap.add_argument("--out-dir", type=Path, default=resolve("data/processed/local_fixture"))
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv = args.out_dir / "hidden_style_synthetic.csv"
    build(args.rows).to_csv(csv, index=False)
    with zipfile.ZipFile(args.out_dir / "hidden_style_synthetic.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(csv, csv.name)
    print(f"wrote {csv} and its ZIP. SYNTHETIC endpoints and timestamps: not evidence of hidden-set performance.")


if __name__ == "__main__":
    main()
