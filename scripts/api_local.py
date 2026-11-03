"""Local API entrypoint with the champion artifact loaded (demo serving)."""

from __future__ import annotations

import json
from pathlib import Path

from risklab.api.main import create_app
from risklab.api.model_store import ModelStore

ROOT = Path(__file__).resolve().parents[1]
serving = json.loads((ROOT / "reports" / "models" / "serving_config.json").read_text(encoding="utf-8"))
store = ModelStore()
store.load_local(
    ROOT / "reports" / "models" / "champion.pkl",
    version="1",
    threshold=float(serving["threshold"]),
    contract_version=str(serving["contract_version"]),
    feature_groups=list(serving["feature_groups"]),
)
app = create_app(store=store, root=ROOT, config_dir=ROOT / "configs")
