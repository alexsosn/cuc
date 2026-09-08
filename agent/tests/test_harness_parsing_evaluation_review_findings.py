from __future__ import annotations

import importlib
import unittest

from reviewed_evaluation.models import MetricSummary


class ParsingEvaluationReviewFindingTest(unittest.TestCase):
    def api(self):
        return importlib.import_module("harness.parsing_evaluation")

    def identity(self, api, *, model_id: str = "model-a"):
        workload = api.ParsingWorkloadRef(
            "CUC",
            "KTU 1.5",
            "I",
            "snapshot-1",
            "sha256:" + "1" * 64,
            "repo-sha",
            "review-automatic-parsing",
            "1.0.0",
            "2" * 64,
            "3" * 64,
            "4" * 64,
            "5" * 64,
        )
        return api.ParsingRunIdentity(
            "run-1",
            workload,
            "provider",
            model_id,
            "version-1",
            "6" * 64,
        )

    def target(
        self,
        api,
        *,
        reviewed_provenance: str = "git-blob:reviewed-a",
        scorer_provenance: str = "git-blob:scorer-a",
    ):
        return api.EvaluationTarget(
            "target-1",
            "reviewed/KTU 1.5.tsv#column-I",
            reviewed_provenance,
            "score_reviewed_morphology.py",
            scorer_provenance,
        )

    def target_with_protocol(
        self,
        api,
        *,
        reviewed_provenance: str = "git-blob:reviewed-a",
        scorer_provenance: str = "git-blob:scorer-a",
        feedback_protocol_sha256: str = "7" * 64,
    ):
        return api.EvaluationTarget(
            target_id="target-1",
            reviewed_ref="reviewed/KTU 1.5.tsv#column-I",
            reviewed_provenance=reviewed_provenance,
            scorer_id="score_reviewed_morphology.py",
            scorer_provenance=scorer_provenance,
            feedback_protocol_sha256=feedback_protocol_sha256,
        )

    def summary(self) -> MetricSummary:
        return MetricSummary(
            compared_ids=1,
            reviewed_option_count=1,
            auto_option_count=1,
            true_positive_option_count=1,
            exact_set_accuracy=1.0,
            macro_precision=1.0,
            macro_recall=1.0,
            macro_f1=1.0,
            macro_jaccard=1.0,
            micro_precision=1.0,
            micro_recall=1.0,
            micro_f1=1.0,
            gold_coverage=1.0,
            mean_extra_options=0.0,
            mean_missing_options=0.0,
            mean_option_count_error=0.0,
        )

    def record(
        self,
        api,
        *,
        target=None,
        decision_revision: int = 2,
        model_id: str = "model-a",
        feedback_revision: int | None = None,
    ):
        feedback = ()
        if feedback_revision is not None:
            feedback = (
                api.ExpertFeedback(
                    "fb-1",
                    "run-1",
                    feedback_revision,
                    api.FeedbackScope.COLUMN,
                    api.FeedbackDisposition.ACCEPT,
                    "expert:reviewer-1",
                    "Column accepted.",
                    ("review:artifact",),
                ),
            )
        return api.ParsingEvaluationRecord(
            schema_version=1,
            identity=self.identity(api, model_id=model_id),
            target=target or self.target(api),
            decision_revision=decision_revision,
            deterministic_measurements=api.measure_morphology_summary(self.summary()),
            supplementary_measurements=(),
            expert_feedback=feedback,
            efficiency=api.EfficiencyMetrics(),
            artifact_refs=("artifact:scorer-json",),
        )

    def test_record_binds_feedback_to_its_exact_state_revision(self) -> None:
        api = self.api()
        with self.assertRaises(ValueError):
            self.record(api, decision_revision=2, feedback_revision=1)

        record = self.record(api, decision_revision=2, feedback_revision=2)
        restored = api.ParsingEvaluationRecord.from_json(record.to_json())
        self.assertEqual(restored.decision_revision, 2)
        self.assertEqual(restored, record)

    def test_record_comparability_rejects_evaluator_and_state_revision_drift(self) -> None:
        api = self.api()
        baseline = self.record(api, model_id="model-a")
        other_model = self.record(api, model_id="model-b")

        model_comparison = api.compare_evaluation_records(
            baseline,
            other_model,
            ignore_model_identity=True,
        )
        self.assertTrue(model_comparison.comparable)
        self.assertEqual(model_comparison.mismatched_dimensions, ())

        drifted = self.record(
            api,
            target=self.target(
                api,
                reviewed_provenance="git-blob:reviewed-b",
                scorer_provenance="git-blob:scorer-b",
            ),
            decision_revision=3,
            model_id="model-b",
        )
        comparison = api.compare_evaluation_records(
            baseline,
            drifted,
            ignore_model_identity=True,
        )
        self.assertFalse(comparison.comparable)
        self.assertEqual(
            comparison.mismatched_dimensions,
            (
                "decision_revision",
                "target.reviewed_provenance",
                "target.scorer_provenance",
            ),
        )

    def test_evaluation_target_requires_feedback_protocol_provenance(self) -> None:
        api = self.api()
        with self.assertRaises(TypeError):
            api.EvaluationTarget(
                "target-legacy",
                "reviewed/KTU 1.5.tsv#column-I",
                "git-blob:reviewed-a",
                "score_reviewed_morphology.py",
                "git-blob:scorer-a",
            )

        target = self.target_with_protocol(api)
        self.assertEqual(target.feedback_protocol_sha256, "7" * 64)
        self.assertEqual(api.EvaluationTarget.from_dict(target.to_dict()), target)
        with self.assertRaises(ValueError):
            self.target_with_protocol(api, feedback_protocol_sha256="not-a-digest")

    def test_record_requires_explicit_state_revision_even_without_feedback(self) -> None:
        api = self.api()
        with self.assertRaises(TypeError):
            api.ParsingEvaluationRecord(
                schema_version=1,
                identity=self.identity(api),
                target=self.target(api),
                deterministic_measurements=api.measure_morphology_summary(self.summary()),
                supplementary_measurements=(),
                expert_feedback=(),
                efficiency=api.EfficiencyMetrics(),
                artifact_refs=(),
            )

    def test_record_comparability_rejects_feedback_protocol_drift(self) -> None:
        api = self.api()
        baseline = self.record(api, target=self.target_with_protocol(api, feedback_protocol_sha256="7" * 64))
        drifted = self.record(api, target=self.target_with_protocol(api, feedback_protocol_sha256="8" * 64))
        report = api.compare_evaluation_records(baseline, drifted)
        self.assertFalse(report.comparable)
        self.assertEqual(report.mismatched_dimensions, ("target.feedback_protocol_sha256",))


if __name__ == "__main__":
    unittest.main()
