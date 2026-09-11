from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path

import pytest

from harness.contracts import TaskSpec
from harness.github_effects import GitHubAction, GitHubEffectRequest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA_A = "a" * 64
SHA_B = "b" * 64


def _mod():
    try:
        return importlib.import_module("harness.systematic_findings")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-017 bridge is not implemented yet: {exc}")


def _occurrence(
    *,
    occurrence_id: str,
    tablet: str = "KTU 1.1",
    column: str = "I",
    token_ids: tuple[str, ...] = ("t1",),
    metric_refs: tuple[str, ...] = (),
    expert_feedback_refs: tuple[str, ...] = (),
):
    mod = _mod()
    return mod.FindingOccurrence(
        occurrence_id=occurrence_id,
        corpus="CUC",
        tablet=tablet,
        column=column,
        token_ids=token_ids,
        summary=f"Observed {occurrence_id}",
        run_id=f"run-{occurrence_id}",
        model_provider="fixture-provider",
        model_name="fixture-model",
        model_version="2026-09-01",
        skill_name="review-automatic-parsing",
        skill_contract_version="1.0.0",
        skill_provenance_sha256=SHA_A,
        evidence_refs=(f"evidence:{occurrence_id}",),
        metric_refs=metric_refs,
        expert_feedback_refs=expert_feedback_refs,
    )


def _candidate(
    *,
    classification=None,
    occurrences=None,
    signals=(),
    positive_cases=("positive:fixture",),
    negative_cases=("negative:fixture",),
    boundary_cases=("boundary:fixture",),
    subsystem="pipeline.tablet-parsing",
    problem_key="verb-stem-overgeneration",
    evidence_refs=("finding:aggregate",),
):
    mod = _mod()
    if classification is None:
        classification = mod.FindingClassification.PARSER_CONFIG_DEFECT
    if occurrences is None:
        occurrences = (
            _occurrence(occurrence_id="a", tablet="KTU 1.1", token_ids=("t1",)),
            _occurrence(occurrence_id="b", tablet="KTU 1.2", token_ids=("t2",)),
        )
    return mod.SystematicFindingCandidate(
        classification=classification,
        subsystem=subsystem,
        problem_key=problem_key,
        title="Parser overgenerates a verb-stem analysis",
        objective="Prevent the systematic overgeneration while preserving supported ambiguity.",
        acceptance_criteria=(
            "reproduce the affected and unaffected cases",
            "add a regression test before the parser fix",
            "regenerate generated parsing rather than hand-editing it",
        ),
        occurrences=occurrences,
        systematic_signals=signals,
        positive_cases=positive_cases,
        negative_cases=negative_cases,
        boundary_cases=boundary_cases,
        evidence_refs=evidence_refs,
    )


def _plan(candidate, existing=()):
    return _mod().plan_development_issue(
        candidate,
        existing_issue_fingerprints=existing,
    )


def test_local_scholarly_reading_never_creates_development_issue():
    mod = _mod()
    candidate = _candidate(
        classification=mod.FindingClassification.LOCAL_SCHOLARLY_READING,
    )
    decision = _plan(candidate)
    assert decision.disposition is mod.FindingDisposition.LOCAL_NO_DEVELOPMENT_ISSUE
    assert decision.task is None
    assert decision.github_request is None


def test_single_token_difference_is_insufficient_without_systematic_signal():
    mod = _mod()
    candidate = _candidate(
        occurrences=(_occurrence(occurrence_id="only"),),
        signals=(),
    )
    decision = _plan(candidate)
    assert decision.disposition is mod.FindingDisposition.INSUFFICIENT_SYSTEMATIC_EVIDENCE
    assert decision.github_request is None


def test_duplicate_observations_of_same_locus_do_not_fake_recurrence():
    mod = _mod()
    candidate = _candidate(
        occurrences=(
            _occurrence(occurrence_id="a", tablet="KTU 1.1", token_ids=("t1",)),
            _occurrence(occurrence_id="b", tablet="KTU 1.1", token_ids=("t1",)),
        )
    )
    decision = _plan(candidate)
    assert decision.disposition is mod.FindingDisposition.INSUFFICIENT_SYSTEMATIC_EVIDENCE


def test_two_distinct_loci_produce_harn010_task_and_fork_issue_request():
    mod = _mod()
    decision = _plan(_candidate())
    assert decision.disposition is mod.FindingDisposition.READY_FOR_DEVELOPMENT_ISSUE
    assert isinstance(decision.task, TaskSpec)
    assert isinstance(decision.github_request, GitHubEffectRequest)
    request = decision.github_request
    assert request.repository == "alexsosn/cuc"
    assert request.action is GitHubAction.CREATE_ISSUE
    assert request.target_ref is None
    assert request.operation_id == f"harn-017:create-issue:{decision.fingerprint}"
    assert decision.task.task_id == f"finding-{decision.fingerprint[:16]}"
    assert request.payload["title"] == decision.task.title
    assert f"harn-017:fingerprint:{decision.fingerprint}" in request.payload["body"]
    assert "DT-UCPH/cuc" not in request.payload["body"]


def test_typed_systematic_signal_can_justify_single_locus():
    mod = _mod()
    signal = mod.SystematicSignal(
        signal_id="eval-regression-1",
        kind=mod.SystematicSignalKind.EVAL_REGRESSION,
        summary="Complete-column exact-set accuracy regressed across the fixed workload.",
        evidence_refs=("eval:run-17",),
    )
    candidate = _candidate(
        classification=mod.FindingClassification.EVAL_BENCHMARK_DEFECT,
        occurrences=(_occurrence(occurrence_id="only", metric_refs=("metric:f1",)),),
        signals=(signal,),
        positive_cases=(),
        negative_cases=(),
        boundary_cases=(),
        subsystem="harness.parsing-evaluation",
        problem_key="metric-artifact-mismatch",
    )
    decision = _plan(candidate)
    assert decision.disposition is mod.FindingDisposition.READY_FOR_DEVELOPMENT_ISSUE


@pytest.mark.parametrize(
    "classification",
    [
        "parser-config-defect",
        "linter-defect",
        "skill-procedure-defect",
    ],
)
def test_rule_like_findings_require_positive_negative_and_boundary_cases(classification):
    mod = _mod()
    candidate = _candidate(
        classification=mod.FindingClassification(classification),
        positive_cases=(),
        negative_cases=(),
        boundary_cases=(),
    )
    decision = _plan(candidate)
    assert decision.disposition is mod.FindingDisposition.INSUFFICIENT_SYSTEMATIC_EVIDENCE
    assert "positive" in decision.reason
    assert "negative" in decision.reason
    assert "boundary" in decision.reason


def test_fingerprint_is_stable_across_occurrence_order_and_added_evidence():
    first = _occurrence(occurrence_id="a", tablet="KTU 1.1", token_ids=("t1",))
    second = _occurrence(occurrence_id="b", tablet="KTU 1.2", token_ids=("t2",))
    base = _candidate(occurrences=(first, second))
    expanded = _candidate(
        occurrences=(
            replace(second, metric_refs=("metric:new",)),
            first,
            _occurrence(occurrence_id="c", tablet="KTU 1.3", token_ids=("t3",)),
        ),
        evidence_refs=("finding:aggregate", "finding:new"),
    )
    assert _plan(base).fingerprint == _plan(expanded).fingerprint


def test_fingerprint_changes_for_distinct_general_problem_identity():
    base = _candidate()
    by_class = _candidate(classification=_mod().FindingClassification.LINTER_DEFECT)
    by_subsystem = _candidate(subsystem="linter.reviewed")
    by_key = _candidate(problem_key="different-defect")
    fingerprints = {
        _plan(base).fingerprint,
        _plan(by_class).fingerprint,
        _plan(by_subsystem).fingerprint,
        _plan(by_key).fingerprint,
    }
    assert len(fingerprints) == 4


def test_existing_fingerprint_suppresses_duplicate_issue_creation():
    mod = _mod()
    candidate = _candidate()
    fingerprint = _plan(candidate).fingerprint
    decision = _plan(candidate, existing=(fingerprint,))
    assert decision.disposition is mod.FindingDisposition.DUPLICATE_EXISTING_ISSUE
    assert decision.task is None
    assert decision.github_request is None


def test_issue_body_contains_reproducible_run_model_skill_metric_and_expert_evidence():
    occurrences = (
        _occurrence(
            occurrence_id="a",
            tablet="KTU 1.1",
            token_ids=("t1",),
            metric_refs=("metric:exact-set",),
            expert_feedback_refs=("expert:review-42",),
        ),
        _occurrence(occurrence_id="b", tablet="KTU 1.2", token_ids=("t2",)),
    )
    body = _plan(_candidate(occurrences=occurrences)).github_request.payload["body"]
    for expected in (
        "KTU 1.1",
        "t1",
        "run-a",
        "fixture-provider",
        "fixture-model",
        "2026-09-01",
        "review-automatic-parsing",
        "1.0.0",
        SHA_A,
        "metric:exact-set",
        "expert:review-42",
    ):
        assert expected in body


def test_invalid_or_underspecified_provenance_and_duplicate_ids_fail_closed():
    mod = _mod()
    with pytest.raises(ValueError, match="skill_provenance"):
        replace(_occurrence(occurrence_id="bad"), skill_provenance_sha256="not-a-digest")
    repeated = _occurrence(occurrence_id="same", tablet="KTU 1.1")
    with pytest.raises(ValueError, match="occurrence"):
        _candidate(occurrences=(repeated, repeated))


def test_module_is_framework_neutral_and_contains_no_dispatch_or_network_adapter():
    mod = _mod()
    source = (REPO_ROOT / "agent/harness/systematic_findings.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in (
        "langgraph",
        "langfuse",
        "requests.",
        "httpx.",
        "urllib.request",
        "execute_write(",
        "githubeffectgateway(",
    ):
        assert forbidden not in lowered
    assert mod.FORK_REPOSITORY == "alexsosn/cuc"
