"""Compatibility entry point: package the selected E3 artifact without refitting.

For a legacy run lacking an E3 artifact, pass --model-path reports/models/champion.pkl.
The artifact must reproduce the stored Selection metrics and pass complete parity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from risklab.models.candidate import prepare_candidate

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    candidate = prepare_candidate(
        root=ROOT, config_dir=ROOT / "configs", model_path=args.model_path
    )
    print(json.dumps(candidate, indent=2))


if __name__ == "__main__":
    main()
