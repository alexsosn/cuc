from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from harness.contracts import RunState, TaskSpec
from harness.development_controller import DevelopmentControllerState
from harness.github_effects import HumanApproval


def _runtime():
    try:
        import harness.development_host as runtime
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-010 durable host is not implemented yet: {exc}")
    return runtime


def _controller_state() -> DevelopmentControllerState:
    task = TaskSpec(
        "issue-53",
        "durable host",
        "persist and restore the trusted development host",
        ("atomic persistence", "trusted approval restore"),
    )
    return DevelopmentControllerState(
        schema_version=1,
        base_sha="a" * 40,
        core=RunState("durable-host-run", task),
        provenance_refs=("issue:53", "research:durable-host"),
        audit_events=("controller-started",),
    )


def _approval() -> HumanApproval:
    return HumanApproval(
        "approval-1",
        "human-reviewer",
        "operation-1",
        "b" * 64,
    )


def test_host_envelope_round_trips_controller_state_and_trusted_approvals() -> None:
    runtime = _runtime()
    envelope = runtime.DevelopmentHostEnvelope(
        schema_version=1,
        controller_state=_controller_state(),
        trusted_approvals=(_approval(),),
    )

    encoded = envelope.to_json()
    restored = runtime.DevelopmentHostEnvelope.from_json(encoded)

    assert restored == envelope
    assert restored.controller_state == _controller_state()
    assert restored.trusted_approvals == (_approval(),)
    assert encoded == json.dumps(
        envelope.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def test_host_envelope_rejects_wrong_schema_unknown_fields_and_duplicate_approval_ids() -> None:
    runtime = _runtime()
    payload = runtime.DevelopmentHostEnvelope(
        1,
        _controller_state(),
        (_approval(),),
    ).to_dict()

    with pytest.raises(ValueError, match="schema"):
        runtime.DevelopmentHostEnvelope.from_dict({**payload, "schema_version": 2})
    with pytest.raises(ValueError, match="unknown|fields"):
        runtime.DevelopmentHostEnvelope.from_dict({**payload, "extra": True})

    duplicate = HumanApproval(
        "approval-1",
        "another-human",
        "operation-2",
        "c" * 64,
    )
    with pytest.raises(ValueError, match="approval"):
        runtime.DevelopmentHostEnvelope(1, _controller_state(), (_approval(), duplicate))


def test_store_distinguishes_missing_state_from_corruption(tmp_path: Path) -> None:
    runtime = _runtime()
    state_path = tmp_path / "host-state.json"
    store = runtime.AtomicJsonDevelopmentHostStore(state_path)

    assert store.load() is None

    state_path.write_text('{"schema_version":1,"controller_state":', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON|state|envelope"):
        store.load()


def test_store_uses_same_directory_temp_atomic_replace_and_canonical_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    state_path = tmp_path / "nested" / "host-state.json"
    store = runtime.AtomicJsonDevelopmentHostStore(state_path)
    envelope = runtime.DevelopmentHostEnvelope(1, _controller_state(), (_approval(),))

    observed_dirs: list[Path] = []
    original_mkstemp = runtime.tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        observed_dirs.append(Path(kwargs["dir"]))
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(runtime.tempfile, "mkstemp", recording_mkstemp)
    store.save(envelope)

    assert observed_dirs == [state_path.parent]
    assert store.load() == envelope
    persisted = state_path.read_text(encoding="utf-8")
    assert persisted == envelope.to_json()
    assert not list(state_path.parent.glob(f".{state_path.name}.*.tmp"))


def test_pre_replace_failure_preserves_previous_valid_snapshot_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    state_path = tmp_path / "host-state.json"
    store = runtime.AtomicJsonDevelopmentHostStore(state_path)
    before = runtime.DevelopmentHostEnvelope(1, _controller_state(), ())
    after = replace(before, trusted_approvals=(_approval(),))
    store.save(before)
    original_bytes = state_path.read_bytes()

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(runtime.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.save(after)

    assert state_path.read_bytes() == original_bytes
    assert runtime.DevelopmentHostEnvelope.from_json(
        state_path.read_text(encoding="utf-8")
    ) == before
    assert not list(tmp_path.glob(f".{state_path.name}.*.tmp"))


def test_production_store_requires_explicit_path() -> None:
    runtime = _runtime()
    with pytest.raises((TypeError, ValueError), match="path|state"):
        runtime.AtomicJsonDevelopmentHostStore(None)
