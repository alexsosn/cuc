from __future__ import annotations

from harness.systematic_findings import (
    FindingClassification,
    FindingOccurrence,
    SystematicFindingCandidate,
    plan_development_issue,
)


SHA = "a" * 64


def _occurrence(occurrence_id: str, tablet: str, locus_ref: str, token_id: str):
    return FindingOccurrence(
        occurrence_id=occurrence_id,
        corpus="CUC",
        tablet=tablet,
        column="I",
        locus_ref=locus_ref,
        token_ids=(token_id,),
        summary=f"Observed {occurrence_id}",
        run_id=f"run-{occurrence_id}",
        model_provider="fixture-provider",
        model_name="fixture-model",
        model_version="2026-09-01",
        skill_name="review-automatic-parsing",
        skill_contract_version="1.0.0",
        skill_provenance_sha256=SHA,
        evidence_refs=(f"evidence:{occurrence_id}",),
    )


def _candidate(occurrences):
    return SystematicFindingCandidate(
        classification=FindingClassification.PARSER_CONFIG_DEFECT,
        subsystem="pipeline.tablet-parsing",
        problem_key="verb-stem-overgeneration",
        title="Parser overgenerates a verb-stem analysis",
        objective="Prevent the systematic overgeneration while preserving supported ambiguity.",
        acceptance_criteria=("add a regression test before the parser fix",),
        occurrences=tuple(occurrences),
        positive_cases=("positive:fixture",),
        negative_cases=("negative:fixture",),
        boundary_cases=("boundary:fixture",),
    )


def test_issue_request_rendering_is_order_invariant_for_case_colliding_ids():
    upper = _occurrence("A", "KTU 1.1", "I:1", "t1")
    lower = _occurrence("a", "KTU 1.2", "I:2", "t2")

    forward = plan_development_issue(_candidate((upper, lower)))
    reverse = plan_development_issue(_candidate((lower, upper)))

    assert forward.fingerprint == reverse.fingerprint
    assert forward.task == reverse.task
    assert forward.github_request == reverse.github_request
