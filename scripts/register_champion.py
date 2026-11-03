"""Register a locked artifact; use the gated CLI to promote it separately."""

from __future__ import annotations

import argparse
from pathlib import Path

from risklab.registry.register import register

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args()
    version, run_id = register(args.lock, root=ROOT, config_dir=ROOT / "configs")
    print(f"Registered version {version} (run {run_id}); promote it with risklab registry promote.")


if __name__ == "__main__":
    main()
