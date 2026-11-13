"""Local lifecycle must not signal a recycled or unidentified PID."""

from scripts import application_local


def test_unidentified_or_recycled_process_is_not_owned(monkeypatch):
    monkeypatch.setattr(application_local, "process_identity", lambda pid: None)
    assert not application_local.alive({"pid": 123, "start_ticks": None})
    assert not application_local.alive({"pid": 123, "start_ticks": "100"})
    monkeypatch.setattr(application_local, "process_identity", lambda pid: "200")
    assert not application_local.alive({"pid": 123, "start_ticks": "100"})
    assert application_local.alive({"pid": 123, "start_ticks": "200"})


def test_stop_leaves_unowned_process_untouched(monkeypatch):
    monkeypatch.setattr(application_local, "process_identity", lambda pid: "new-owner")

    def forbidden(*args):
        raise AssertionError("Signaled another process")

    monkeypatch.setattr(application_local.os, "killpg", forbidden)
    application_local.stop_process({"pid": 123, "start_ticks": "old-owner"})


def test_missing_catalog_is_detected_before_model_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(application_local, "DEMO", tmp_path)
    for name in ("release.json", "model.pkl", "comparison.json", "import.csv"):
        (tmp_path / name).write_text("synthetic placeholder")
    import pytest

    with pytest.raises(RuntimeError, match="missing catalog.json"):
        application_local.verify_model()


def test_stop_retains_ownership_of_surviving_process(tmp_path, monkeypatch):
    import json

    import pytest

    monkeypatch.setattr(application_local, "RUN", tmp_path)
    monkeypatch.setattr(application_local.sys, "argv", ["local", "stop"])
    monkeypatch.setattr(application_local, "alive", lambda _: True)
    monkeypatch.setattr(application_local, "stop_process", lambda _: None)
    record = {"worker": {"pid": 123, "start_ticks": "100"}}
    application_local.private_json(tmp_path / "processes.json", record)
    with pytest.raises(RuntimeError, match="ownership records retained"):
        application_local.main()
    assert json.loads((tmp_path / "processes.json").read_text()) == record
