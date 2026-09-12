from __future__ import annotations

import json

import pytest

import harness.production_host as production_host
from harness.contracts import RunState, TaskSpec
from harness.development_controller import DevelopmentControllerState
from harness.github_effects import HumanApproval
from harness.production_host import AtomicHostStateStore, ProductionHostEnvelope


BASE = "a" * 40
OTHER_BASE = "b" * 40
DIGEST = "c" * 64


def _task() -> TaskSpec:
    return TaskSpec(
        "issue-53",
        "durable production host",
        "restore controller and trusted approval state after process restart",
        ("production state is durable and fail-closed",),
    )


def _state(base_sha: str = BASE, *, github_writes: int = 0) -> DevelopmentControllerState:
    return DevelopmentControllerState(
        schema_version=1,
        base_sha=base_sha,
        core=RunState("run-53", _task()),
        provenance_refs=("issue:53",),
        github_writes=github_writes,
    )


def _approval(
    *, approval_id: str = "approval-1", operation_id: str = "operation-1"
) -> HumanApproval:
    return HumanApproval(
        approval_id,
        "human-reviewer",
        operation_id,
        DIGEST,
    )


def test_host_envelope_round_trips_controller_state_and_trusted_approvals() -> None:
    envelope = ProductionHostEnvelope(
        schema_version=1,
        controller_state=_state(),
        trusted_approvals=(_approval(),),
    )

    restored = ProductionHostEnvelope.from_dict(envelope.to_dict())

    assert restored == envelope
    assert restored.controller_state is not None
    assert restored.controller_state.base_sha == BASE
    assert restored.trusted_approvals == (_approval(),)


def test_host_envelope_rejects_wrong_schema_and_duplicate_authority_records() -> None:
    valid = ProductionHostEnvelope(1, _state(), (_approval(),)).to_dict()

    wrong_schema = dict(valid)
    wrong_schema["schema_version"] = 999
    with pytest.raises(ValueError, match="schema"):
        ProductionHostEnvelope.from_dict(wrong_schema)

    duplicate_id = dict(valid)
    duplicate_id["trusted_approvals"] = [
        _approval().to_dict(),
        HumanApproval("approval-1", "other-human", "operation-2", "d" * 64).to_dict(),
    ]
    with pytest.raises(ValueError, match="approval.*unique|unique.*approval"):
        ProductionHostEnvelope.from_dict(duplicate_id)

    duplicate_operation = dict(valid)
    duplicate_operation["trusted_approvals"] = [
        _approval().to_dict(),
        HumanApproval("approval-2", "other-human", "operation-1", DIGEST).to_dict(),
    ]
    with pytest.raises(ValueError, match="operation.*unique|unique.*operation"):
        ProductionHostEnvelope.from_dict(duplicate_operation)


def test_host_envelope_rejects_valid_json_missing_required_state_fields() -> None:
    with pytest.raises(ValueError, match="controller_state|trusted_approvals|required"):
        ProductionHostEnvelope.from_dict({"schema_version": 1})

    with pytest.raises(ValueError, match="trusted_approvals|required"):
        ProductionHostEnvelope.from_dict(
            {"schema_version": 1, "controller_state": _state().to_dict()}
        )

    with pytest.raises(ValueError, match="controller_state|required"):
        ProductionHostEnvelope.from_dict(
            {"schema_version": 1, "trusted_approvals": []}
        )


def test_host_envelope_detects_nested_controller_state_truncation() -> None:
    payload = ProductionHostEnvelope(1, _state(github_writes=1), ()).to_dict()
    controller = dict(payload["controller_state"])
    assert controller.pop("github_writes") == 1
    payload["controller_state"] = controller

    with pytest.raises(ValueError, match="integrity|digest|corrupt|truncat"):
        ProductionHostEnvelope.from_dict(payload)


def test_atomic_store_round_trips_and_corrupt_existing_state_fails_closed(tmp_path) -> None:
    path = tmp_path / "development-host.json"
    store = AtomicHostStateStore(path)
    envelope = ProductionHostEnvelope(1, _state(), (_approval(),))

    assert store.load() is None
    store.save(envelope)
    assert store.load() == envelope

    path.write_text('{"schema_version": 1, "controller_state":', encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        store.load()


def test_atomic_store_pre_replace_failure_preserves_previous_valid_snapshot(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "development-host.json"
    store = AtomicHostStateStore(path)
    original = ProductionHostEnvelope(1, _state(BASE), (_approval(),))
    replacement = ProductionHostEnvelope(1, _state(OTHER_BASE), (_approval(),))
    store.save(original)

    def fail_replace(_source, _target):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(production_host.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        store.save(replacement)

    assert store.load() == original
    assert not tuple(tmp_path.glob(".development-host.json.*.tmp"))


def test_atomic_store_requires_an_explicit_nonempty_state_path() -> None:
    with pytest.raises(ValueError, match="path"):
        AtomicHostStateStore("")
