"""Feature registry (``configs/features.yaml``): groups, SQL files and column contract."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from risklab.config import _Strict, load_model

REGISTRY_FILE = "features.yaml"


class FeatureDef(_Strict):
    name: str = Field(min_length=1)
    dtype: str = "float64"
    nullable: bool = True


class FeatureGroup(_Strict):
    source_sql: str
    source_tables: list[str]
    features: list[FeatureDef] = Field(min_length=1)


class FeatureRegistry(_Strict):
    contract_version: str = "1.0"
    groups: dict[str, FeatureGroup]

    def group_features(self, groups: list[str]) -> list[str]:
        out: list[str] = []
        for g in groups:
            if g not in self.groups:
                raise ValueError(f"unknown feature group {g!r}")
            out.extend(f.name for f in self.groups[g].features)
        return out

    def all_groups(self) -> list[str]:
        return list(self.groups)

    def sql_path(self, group: str, *, project_root: Path) -> Path:
        return project_root / self.groups[group].source_sql


def load_registry(config_dir: Path | str | None = None) -> FeatureRegistry:
    return load_model(config_dir, REGISTRY_FILE, FeatureRegistry)


__all__ = ["FeatureDef", "FeatureGroup", "FeatureRegistry", "load_registry"]
