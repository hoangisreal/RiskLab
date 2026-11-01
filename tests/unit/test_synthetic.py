import csv
from pathlib import Path

import pytest

from risklab.config import DataConfig, RawSchema
from tests.fixtures.synthetic import CORE_TABLES, generate

pytestmark = pytest.mark.unit

N = 1000
SEED = 7


def _csv_names(cfg: DataConfig) -> dict[str, str]:
    return {t: f for f, t in cfg.tables.items()}


def _read(path: Path) -> dict[str, list[str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        cols: dict[str, list[str]] = {h: [] for h in header}
        for row in reader:
            assert len(row) == len(header)
            for h, v in zip(header, row, strict=True):
                cols[h].append(v)
    return cols


@pytest.fixture(scope="module")
def synth(
    tmp_path_factory: pytest.TempPathFactory, repo_data_config: DataConfig, raw_schema: RawSchema
) -> dict[str, dict[str, list[str]]]:
    out = tmp_path_factory.mktemp("synth")
    generate(out, raw_schema, _csv_names(repo_data_config), n_applicants=N, seed=SEED)
    names = _csv_names(repo_data_config)
    return {t: _read(out / names[t]) for t in CORE_TABLES}


def test_same_seed_is_byte_identical_and_other_seed_differs(
    tmp_path: Path, repo_data_config: DataConfig, raw_schema: RawSchema
) -> None:
    names = _csv_names(repo_data_config)
    for sub, seed in (("a", 1), ("b", 1), ("c", 2)):
        generate(tmp_path / sub, raw_schema, names, n_applicants=200, seed=seed)
    for fname in names.values():
        assert (tmp_path / "a" / fname).read_bytes() == (tmp_path / "b" / fname).read_bytes()
    assert (tmp_path / "a" / "bureau.csv").read_bytes() != (
        tmp_path / "c" / "bureau.csv"
    ).read_bytes()


def test_headers_match_schema(
    synth: dict[str, dict[str, list[str]]], raw_schema: RawSchema
) -> None:
    for table in CORE_TABLES:
        assert list(synth[table]) == [c.name for c in raw_schema.columns(table)]
    assert "TARGET" not in synth["application_test"]


def test_key_integrity(synth: dict[str, dict[str, list[str]]]) -> None:
    train, test = synth["application_train"]["SK_ID_CURR"], synth["application_test"]["SK_ID_CURR"]
    assert len(train) + len(test) == N
    assert len(set(train) | set(test)) == N
    apps = set(train) | set(test)
    bureau_ids = synth["bureau"]["SK_ID_BUREAU"]
    assert len(set(bureau_ids)) == len(bureau_ids)
    assert set(synth["bureau"]["SK_ID_CURR"]) <= apps
    assert set(synth["bureau_balance"]["SK_ID_BUREAU"]) <= set(bureau_ids)
    prev = synth["previous_application"]
    assert len(set(prev["SK_ID_PREV"])) == len(prev["SK_ID_PREV"])
    owner = dict(zip(prev["SK_ID_PREV"], prev["SK_ID_CURR"], strict=True))
    for table in ("pos_cash_balance", "credit_card_balance", "installments_payments"):
        t = synth[table]
        assert set(t["SK_ID_PREV"]) <= set(owner)
        assert all(owner[p] == c for p, c in zip(t["SK_ID_PREV"], t["SK_ID_CURR"], strict=True))


def test_target_prevalence(synth: dict[str, dict[str, list[str]]]) -> None:
    target = [int(v) for v in synth["application_train"]["TARGET"]]
    assert set(target) <= {0, 1}
    assert 0.05 <= sum(target) / len(target) <= 0.11


def test_signal_is_learnable(synth: dict[str, dict[str, list[str]]]) -> None:
    train = synth["application_train"]
    pairs = [
        (float(e), int(t))
        for e, t in zip(train["EXT_SOURCE_2"], train["TARGET"], strict=True)
        if e != ""
    ]
    pos = [e for e, t in pairs if t == 1]
    neg = [e for e, t in pairs if t == 0]
    assert sum(pos) / len(pos) < sum(neg) / len(neg) - 0.05


def test_edge_cases_present(synth: dict[str, dict[str, list[str]]]) -> None:
    train = synth["application_train"]
    assert "365243" in train["DAYS_EMPLOYED"]
    assert "XNA" in train["CODE_GENDER"]
    assert "XNA" in train["ORGANIZATION_TYPE"]
    prev = synth["previous_application"]
    for col in ("DAYS_FIRST_DRAWING", "DAYS_FIRST_DUE", "DAYS_LAST_DUE", "DAYS_TERMINATION"):
        assert any(v.startswith("365243") for v in prev[col]), col
    assert "XNA" in prev["NAME_PAYMENT_TYPE"]
    assert "" in train["EXT_SOURCE_1"] and "" in train["OWN_CAR_AGE"]
    with_history = set(synth["bureau"]["SK_ID_CURR"]) | set(prev["SK_ID_CURR"])
    all_apps = set(train["SK_ID_CURR"]) | set(synth["application_test"]["SK_ID_CURR"])
    assert all_apps - set(synth["bureau"]["SK_ID_CURR"])  # no bureau
    assert all_apps - set(prev["SK_ID_CURR"])  # no previous applications
    assert all_apps - with_history  # neither


def test_time_field_signs(synth: dict[str, dict[str, list[str]]]) -> None:
    def nums(table: str, col: str) -> list[float]:
        return [float(v) for v in synth[table][col] if v != ""]

    assert max(nums("bureau_balance", "MONTHS_BALANCE")) == 0  # snapshot at application
    assert any(v <= -1 for v in nums("bureau_balance", "MONTHS_BALANCE"))
    assert max(nums("pos_cash_balance", "MONTHS_BALANCE")) <= -1
    assert max(nums("credit_card_balance", "MONTHS_BALANCE")) <= -1
    assert max(nums("bureau", "DAYS_CREDIT")) <= -1
    assert max(nums("bureau", "DAYS_ENDDATE_FACT")) <= 0
    assert max(nums("installments_payments", "DAYS_INSTALMENT")) <= -1
    assert max(nums("installments_payments", "DAYS_ENTRY_PAYMENT")) <= 0
    assert max(nums("application_train", "DAYS_BIRTH")) < 0
    employed = [v for v in nums("application_train", "DAYS_EMPLOYED") if v != 365243]
    assert max(employed) < 0
    assert max(nums("previous_application", "DAYS_DECISION")) <= -1


def test_too_few_applicants_rejected(
    tmp_path: Path, repo_data_config: DataConfig, raw_schema: RawSchema
) -> None:
    with pytest.raises(ValueError, match="n_applicants"):
        generate(tmp_path, raw_schema, _csv_names(repo_data_config), n_applicants=10, seed=1)
