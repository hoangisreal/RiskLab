"""Extract official training-only downloads and bind their bytes without reading labels."""

import argparse
import json
import shutil
import zipfile
from pathlib import Path

from risklab.manifest import sha256_file, write_json_atomic


def prepare(data_root: Path) -> dict:
    raw = data_root / "raw"
    expected = ["train_base.parquet", "train_static_0_0.parquet", "train_static_0_1.parquet"]
    for name in expected:
        archive = raw / (name + ".zip")
        target = raw / name
        with zipfile.ZipFile(archive) as zipped:
            if zipped.namelist() != [name]:
                raise ValueError("archive must contain exactly the requested training file")
            if not target.exists():
                with zipped.open(name) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
    paths = [raw / n for n in expected] + [raw / (n + ".zip") for n in expected]
    paths.append(raw / "feature_definitions.csv")
    manifest = {
        "source": "Kaggle official competition home-credit-credit-risk-model-stability",
        "files": {p.name: sha256_file(p) for p in paths},
        "competition_test_downloaded": False,
        "labels_opened": False,
    }
    target = data_root / "acquisition.json"
    if target.exists():
        if json.loads(target.read_text()) != manifest:
            raise ValueError(
                "acquisition differs; use a fresh data root, do not overwrite evidence"
            )
    else:
        write_json_atomic(target, manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.data_root.resolve())
    print(f"Verified {len(result['files'])} source files; targets not read.")
