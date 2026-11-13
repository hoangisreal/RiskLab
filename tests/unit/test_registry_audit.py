"""Alias mutations have a durable intent even when completion logging fails."""

import pytest

from risklab.registry import audit, client


def test_intent_write_failure_never_changes_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "champion_info_or_none", lambda: {"version": "1"})
    monkeypatch.setattr(client, "set_alias", lambda _: pytest.fail("unrecorded mutation"))

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(audit, "append_audit", fail)
    with pytest.raises(OSError):
        audit.change_alias(
            tmp_path / "audit.jsonl", action="promote", from_version="1", to_version="2"
        )


def test_incomplete_intent_is_reconciled_without_replaying_remote_change(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    state = {"version": "1"}
    calls = []
    monkeypatch.setattr(client, "champion_info_or_none", lambda: dict(state))

    def set_alias(version):
        state["version"] = version
        calls.append(version)

    monkeypatch.setattr(client, "set_alias", set_alias)
    original = audit.append_audit

    def fail_completion(*args, **kwargs):
        if kwargs["action"] == "promote":
            raise OSError("synthetic disk failure after mutation")
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, "append_audit", fail_completion)
    with pytest.raises(OSError):
        audit.change_alias(path, action="promote", from_version="1", to_version="2")
    assert audit.read_audit(path)[0]["action"] == "promote.intent"
    monkeypatch.setattr(audit, "append_audit", original)
    with pytest.raises(client.RegistryError, match="champion changed"):
        audit.change_alias(path, action="promote", from_version="1", to_version="2")
    assert calls == ["2"]
    history = audit.read_audit(path)
    assert [e["action"] for e in history] == ["promote.intent", "promote"]
    assert history[0]["operation_id"] == history[1]["operation_id"]
