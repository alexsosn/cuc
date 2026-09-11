from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    return importlib.import_module("harness.github_effects")


def _safety_module():
    path = REPO_ROOT / "agent" / "tests" / "test_repository_safety.py"
    spec = importlib.util.spec_from_file_location("harn009_repository_safety", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Adapter:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return "fake:created"


def test_fork_operation_id_is_bound_to_one_declared_action_not_policy_cross_product():
    runtime = _runtime()
    adapter = _Adapter()
    policy = runtime.GitHubTaskPolicy(
        allowed_fork_write_operations=(
            runtime.GitHubOperationPermission(
                "op-issue", runtime.GitHubAction.CREATE_ISSUE
            ),
            runtime.GitHubOperationPermission(
                "op-branch", runtime.GitHubAction.CREATE_BRANCH
            ),
        ),
        allowed_upstream_operation_ids=(),
    )
    gateway = runtime.GitHubEffectGateway(policy, adapter)
    repurposed = runtime.GitHubEffectRequest(
        "op-branch",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "must not inherit another operation's capability"},
    )

    with pytest.raises(PermissionError, match="operation|action|policy|capability"):
        gateway.execute_write(
            repurposed,
            runtime.GitHubEffectJournal(),
            checkpoint=lambda _journal: None,
        )
    assert adapter.calls == []


def test_failed_success_receipt_checkpoint_is_reported_as_outcome_unknown_and_not_retryable():
    runtime = _runtime()
    adapter = _Adapter()
    policy = runtime.GitHubTaskPolicy(
        allowed_fork_write_operations=(
            runtime.GitHubOperationPermission(
                "op-receipt-window", runtime.GitHubAction.CREATE_ISSUE
            ),
        ),
        allowed_upstream_operation_ids=(),
    )
    gateway = runtime.GitHubEffectGateway(policy, adapter)
    request = runtime.GitHubEffectRequest(
        "op-receipt-window",
        "alexsosn/cuc",
        runtime.GitHubAction.CREATE_ISSUE,
        {"title": "write may succeed before receipt persistence fails"},
    )
    persisted = []

    def checkpoint(journal):
        if not persisted:
            persisted.append(runtime.GitHubEffectJournal.from_dict(journal.to_dict()))
            return
        raise RuntimeError("receipt store unavailable")

    with pytest.raises(runtime.GitHubEffectOutcomeUnknown) as captured:
        gateway.execute_write(
            request,
            runtime.GitHubEffectJournal(),
            checkpoint=checkpoint,
        )

    assert len(adapter.calls) == 1
    assert captured.value.journal == persisted[0]
    assert captured.value.journal.uncertain_operations == (request.operation_id,)
    assert isinstance(captured.value.cause, RuntimeError)

    with pytest.raises(runtime.GitHubEffectOutcomeUnknown):
        gateway.execute_write(
            request,
            captured.value.journal,
            checkpoint=lambda _journal: pytest.fail("retry must not checkpoint"),
        )
    assert len(adapter.calls) == 1


def test_static_safety_guard_scans_harness_for_generic_direct_github_transport():
    text = (REPO_ROOT / "agent" / "tests" / "test_repository_safety.py").read_text(
        encoding="utf-8"
    )
    assert "def test_development_harness_has_no_direct_github_transport" in text
    assert "DIRECT_GITHUB_TRANSPORT_MARKERS" in text


def test_static_transport_guard_allows_local_processes_but_detects_direct_github_io():
    safety = _safety_module()
    assert not safety._contains_direct_github_transport(
        'subprocess.run(["python", "-m", "pytest", "-q"], check=True)'
    )
    assert not safety._contains_direct_github_transport(
        'requests.post("https://example.invalid/evals", json=payload)'
    )
    assert safety._contains_direct_github_transport(
        'requests.post("https://api.github.com/repos/owner/repo/issues", json=payload)'
    )
    assert safety._contains_direct_github_transport('os.system("gh issue create --title x")')
