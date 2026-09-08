from __future__ import annotations

import importlib
import json
import math
import unittest
from dataclasses import replace
from pathlib import Path

from reviewed_evaluation.models import MetricSummary


REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_MODULE = REPO_ROOT / "agent" / "harness" / "parsing_evaluation.py"
FIXTURE_MANIFEST = (
    REPO_ROOT
    / "agent"
    / "tests"
    / "fixtures"
    / "harn_003_reviewed_morphology"
    / "manifest.json"
)


class ParsingEvaluationProtocolTest(unittest.TestCase):
    maxDiff = None

    def api(self):
        try:
            return importlib.import_module("harness.parsing_evaluation")
        except ModuleNotFoundError as exc:
            self.fail(f"HARN-015 parsing evaluation protocol is not implemented yet: {exc}")

    def workload(self, api, **overrides):
        values = {
            "corpus": "CUC",
            "tablet": "KTU 1.5",
            "column": "I",
            "snapshot_id": "snapshot-ktu-1.5-I",
            "snapshot_provenance": "sha256:" + "1" * 64,
            "repository_revision": "87b059d6",
            "capability_name": "review-automatic-parsing",
            "capability_contract_version": "1.0.0",
            "capability_provenance_sha256": "2" * 64,
            "tool_policy_sha256": "3" * 64,
            "evidence_policy_sha256": "4" * 64,
            "permission_policy_sha256": "5" * 64,
        }
        values.update(overrides)
        return api.ParsingWorkloadRef(**values)

    def identity(self, api, **overrides):
        values = {
            "run_id": "column-run-1",
            "workload": self.workload(api),
            "model_provider": "provider-a",
            "model_id": "model-a",
            "model_version": "2026-09-08",
            "model_config_sha256": "6" * 64,
        }
        values.update(overrides)
        return api.ParsingRunIdentity(**values)

    def target(self, api):
        return api.EvaluationTarget(
            target_id="reviewed-ktu-1.5-I",
            reviewed_ref="reviewed/KTU 1.5.tsv#column-I",
            reviewed_provenance="git-blob:abc123",
            scorer_id="score_reviewed_morphology.py",
            scorer_provenance="git-blob:def456",
            feedback_protocol_sha256="7" * 64,
        )

    def column_state(self):
        column = importlib.import_module("harness.column_state")
        task = column.ColumnTask(
            "column-run-1",
            "CUC",
            "KTU 1.5",
            "I",
            "87b059d6",
            column.CapabilityRef(
                "review-automatic-parsing",
                "1.0.0",
                "2" * 64,
            ),
            ("review-status-clean",),
            (),
        )
        snapshot = column.ColumnSnapshot(
            "snapshot-ktu-1.5-I",
            "auto_parsing/example.tsv",
            "sha256:" + "1" * 64,
            (
                column.ColumnToken("t1", 1, "1.5:I:1", "a"),
                column.ColumnToken("t2", 2, "1.5:I:2", "b"),
            ),
        )
        state = column.ColumnRunState.initial(task, snapshot)
        state = column.apply_column_event(
            state,
            column.EvidenceRecorded(
                "ev-event",
                column.EvidenceRecord(
                    "ev1",
                    "dulat",
                    "entry",
                    "dulat:sha256:123",
                    "Evidence.",
                ),
            ),
        )
        state = column.apply_column_event(
            state,
            column.TokenReviewed(
                "review-t1",
                column.TokenDecision(
                    "d1",
                    "t1",
                    ("analysis-a", "analysis-b"),
                    ("ev1",),
                    "Ambiguity preserved.",
                ),
            ),
        )
        state = column.apply_column_event(
            state,
            column.TokenReviewed(
                "review-t2",
                column.TokenDecision(
                    "d2",
                    "t2",
                    ("analysis-c",),
                    ("ev1",),
                    "Reviewed.",
                ),
            ),
        )
        return column, state

    def manifest_summary(self):
        payload = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        return payload["expected_summary"]

    def test_run_identity_round_trip_requires_exact_provenance(self) -> None:
        api = self.api()
        identity = self.identity(api)

        encoded = identity.to_json()
        self.assertEqual(encoded, identity.to_json())
        self.assertEqual(api.ParsingRunIdentity.from_json(encoded), identity)

        with self.assertRaises(ValueError):
            self.identity(api, model_version="")
        with self.assertRaises(ValueError):
            self.identity(api, model_config_sha256="not-a-digest")
        with self.assertRaises(ValueError):
            self.workload(api, capability_provenance_sha256="short")
        with self.assertRaises(ValueError):
            self.workload(api, tool_policy_sha256="")

    def test_comparability_reports_drift_and_can_ignore_model_identity_only(self) -> None:
        api = self.api()
        baseline = self.identity(api)
        other_model = self.identity(
            api,
            model_provider="provider-b",
            model_id="model-b",
            model_version="v2",
            model_config_sha256="7" * 64,
        )

        strict = api.compare_run_identities(baseline, other_model)
        self.assertFalse(strict.comparable)
        self.assertEqual(
            strict.mismatched_dimensions,
            (
                "model_config_sha256",
                "model_id",
                "model_provider",
                "model_version",
            ),
        )

        backend = api.compare_run_identities(
            baseline,
            other_model,
            ignore_model_identity=True,
        )
        self.assertTrue(backend.comparable)
        self.assertEqual(backend.mismatched_dimensions, ())

        drifted_workload = replace(
            other_model,
            workload=self.workload(
                api,
                snapshot_id="different-snapshot",
                evidence_policy_sha256="8" * 64,
            ),
        )
        drift = api.compare_run_identities(
            baseline,
            drifted_workload,
            ignore_model_identity=True,
        )
        self.assertFalse(drift.comparable)
        self.assertEqual(
            drift.mismatched_dimensions,
            ("evidence_policy_sha256", "snapshot_id"),
        )

    def test_model_context_metadata_excludes_gold_feedback_and_scores(self) -> None:
        api = self.api()
        measurement = api.EvaluationMeasurement(
            "morphology.exact_set_accuracy",
            api.MeasurementKind.NUMERIC,
            0.5,
            api.MeasurementScope.RUN,
            "reviewed-morphology-scorer",
            ("scorer:sha",),
            deterministic=True,
        )
        feedback = api.ExpertFeedback(
            "fb1",
            "column-run-1",
            2,
            api.FeedbackScope.TOKEN,
            api.FeedbackDisposition.CORRECT,
            "expert:reviewer-1",
            "Expert correction.",
            ("publication:ref",),
            token_id="t1",
            decision_id="d1",
            corrected_analyses=("corrected-a", "corrected-b"),
        )
        record = api.ParsingEvaluationRecord(
            1,
            self.identity(api),
            self.target(api),
            2,
            (measurement,),
            (),
            (feedback,),
            api.EfficiencyMetrics(),
            ("artifact:raw-scorer-json",),
        )

        context = record.model_context_metadata()
        encoded = json.dumps(context, sort_keys=True).lower()
        self.assertEqual(context, record.identity.model_context_metadata())
        for forbidden in (
            "reviewed_ref",
            "reviewed_provenance",
            "evaluation_target",
            "feedback",
            "corrected",
            "morphology.exact_set_accuracy",
            "expert correction",
            "raw-scorer",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_measurement_type_scope_and_numeric_validation(self) -> None:
        api = self.api()
        token_measurement = api.EvaluationMeasurement(
            "behavior.accepted",
            api.MeasurementKind.BOOLEAN,
            True,
            api.MeasurementScope.TOKEN,
            "expert-feedback",
            ("feedback:fb1",),
            token_id="t1",
            decision_id="d1",
            deterministic=False,
        )
        self.assertEqual(
            api.EvaluationMeasurement.from_dict(token_measurement.to_dict()),
            token_measurement,
        )

        with self.assertRaises(ValueError):
            api.EvaluationMeasurement(
                "bad.numeric",
                api.MeasurementKind.NUMERIC,
                True,
                api.MeasurementScope.RUN,
                "test",
            )
        with self.assertRaises(ValueError):
            api.EvaluationMeasurement(
                "bad.inf",
                api.MeasurementKind.NUMERIC,
                math.inf,
                api.MeasurementScope.RUN,
                "test",
            )
        with self.assertRaises(ValueError):
            api.EvaluationMeasurement(
                "bad.token",
                api.MeasurementKind.CATEGORICAL,
                "x",
                api.MeasurementScope.TOKEN,
                "test",
            )
        with self.assertRaises(ValueError):
            api.EvaluationMeasurement(
                "bad.column",
                api.MeasurementKind.BOOLEAN,
                True,
                api.MeasurementScope.COLUMN,
                "test",
                token_id="t1",
            )

    def test_reviewed_summary_adapter_is_lossless_and_complete(self) -> None:
        api = self.api()
        expected = self.manifest_summary()
        summary = MetricSummary(**expected)
        measurements = api.measure_morphology_summary(
            summary,
            provenance_refs=("fixture-manifest:73c14c8", "scorer:dacfffbd"),
        )

        by_name = {item.name: item for item in measurements}
        self.assertEqual(
            set(by_name),
            {f"morphology.{name}" for name in expected},
        )
        self.assertEqual(
            {name.removeprefix("morphology."): item.value for name, item in by_name.items()},
            expected,
        )
        self.assertTrue(all(item.deterministic for item in measurements))
        self.assertTrue(
            all(item.source == "reviewed-morphology-scorer" for item in measurements)
        )
        source = PROTOCOL_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("MorphologyAgreementScorer", source)
        self.assertNotIn("score_file_pair", source)

    def test_harn_003_fixture_baseline_maps_without_metric_drift(self) -> None:
        api = self.api()
        expected = self.manifest_summary()
        summary = MetricSummary(**expected)
        encoded = [item.to_dict() for item in api.measure_morphology_summary(summary)]

        values = {item["name"]: item["value"] for item in encoded}
        self.assertEqual(len(values), len(expected))
        for name, value in expected.items():
            self.assertEqual(values[f"morphology.{name}"], value)

    def test_expert_feedback_contract_preserves_correction_alternatives(self) -> None:
        api = self.api()
        feedback = api.ExpertFeedback(
            "fb1",
            "column-run-1",
            2,
            api.FeedbackScope.TOKEN,
            api.FeedbackDisposition.CORRECT,
            "expert:reviewer-1",
            "Both readings remain defensible.",
            ("DULAT:entry", "Tropper:p.123"),
            token_id="t1",
            decision_id="d1",
            corrected_analyses=("analysis-a", "analysis-b"),
        )
        restored = api.ExpertFeedback.from_json(feedback.to_json())
        self.assertEqual(restored, feedback)
        self.assertEqual(restored.corrected_analyses, ("analysis-a", "analysis-b"))

        with self.assertRaises(ValueError):
            replace(feedback, corrected_analyses=())
        with self.assertRaises(ValueError):
            replace(
                feedback,
                disposition=api.FeedbackDisposition.ACCEPT,
                corrected_analyses=("should-not-be-here",),
            )
        with self.assertRaises(ValueError):
            replace(feedback, token_id=None)
        with self.assertRaises(ValueError):
            api.ExpertFeedback(
                "column-feedback",
                "column-run-1",
                2,
                api.FeedbackScope.COLUMN,
                api.FeedbackDisposition.ACCEPT,
                "expert:reviewer-1",
                "Column accepted.",
                ("review:ref",),
                token_id="t1",
                decision_id="d1",
            )

    def test_feedback_validates_against_exact_column_run_revision_and_decision(self) -> None:
        api = self.api()
        _, state = self.column_state()
        feedback = api.ExpertFeedback(
            "fb1",
            state.task.task_id,
            state.decision_revision,
            api.FeedbackScope.TOKEN,
            api.FeedbackDisposition.ACCEPT,
            "expert:reviewer-1",
            "Accepted.",
            ("expert-log:1",),
            token_id="t1",
            decision_id="d1",
        )
        api.validate_feedback_against_state(feedback, state)

        with self.assertRaises(ValueError):
            api.validate_feedback_against_state(
                replace(feedback, run_id="other-run"),
                state,
            )
        with self.assertRaises(ValueError):
            api.validate_feedback_against_state(
                replace(feedback, decision_revision=1),
                state,
            )
        with self.assertRaises(ValueError):
            api.validate_feedback_against_state(
                replace(feedback, token_id="missing-token"),
                state,
            )
        with self.assertRaises(ValueError):
            api.validate_feedback_against_state(
                replace(feedback, decision_id="missing-decision"),
                state,
            )

    def test_column_behavior_measurements_are_separate_from_output_quality(self) -> None:
        api = self.api()
        _, state = self.column_state()
        measurements = api.measure_column_behavior(state)
        values = {item.name: item.value for item in measurements}

        self.assertEqual(values["behavior.expected_token_count"], 2)
        self.assertEqual(values["behavior.visited_initial_tokens"], 2)
        self.assertEqual(values["behavior.revisit_decision_count"], 0)
        self.assertEqual(values["behavior.unresolved_revisit_count"], 0)
        self.assertEqual(values["behavior.reconciliation_finding_count"], 0)
        self.assertEqual(values["behavior.unresolved_required_finding_count"], 0)
        self.assertEqual(values["behavior.latest_ambiguous_token_count"], 1)
        self.assertEqual(values["behavior.column_completed"], False)
        self.assertTrue(all(item.deterministic for item in measurements))
        self.assertTrue(all(not item.name.startswith("morphology.") for item in measurements))

    def test_efficiency_metrics_distinguish_missing_from_observed_zero(self) -> None:
        api = self.api()
        missing = api.EfficiencyMetrics()
        self.assertIsNone(missing.model_calls)
        self.assertIsNone(missing.tool_calls)
        self.assertIsNone(missing.latency_ms)
        self.assertIsNone(missing.cost)

        observed_zero = api.EfficiencyMetrics(
            model_calls=0,
            tool_calls=0,
            retries=0,
            latency_ms=0.0,
            input_tokens=0,
            output_tokens=0,
        )
        self.assertEqual(observed_zero.model_calls, 0)
        self.assertEqual(observed_zero.latency_ms, 0.0)
        self.assertNotEqual(missing.to_dict(), observed_zero.to_dict())

        with self.assertRaises(ValueError):
            api.EfficiencyMetrics(model_calls=-1)
        with self.assertRaises(ValueError):
            api.EfficiencyMetrics(latency_ms=math.nan)
        with self.assertRaises(ValueError):
            api.EfficiencyMetrics(cost=1.25)
        priced = api.EfficiencyMetrics(cost=1.25, currency="USD")
        self.assertEqual(priced.currency, "USD")

    def test_evaluation_record_round_trip_and_measurement_uniqueness(self) -> None:
        api = self.api()
        deterministic = api.measure_morphology_summary(
            MetricSummary(**self.manifest_summary())
        )
        record = api.ParsingEvaluationRecord(
            1,
            self.identity(api),
            self.target(api),
            2,
            deterministic,
            (),
            (),
            api.EfficiencyMetrics(model_calls=1, tool_calls=3),
            ("artifact:scorer-json",),
        )
        encoded = record.to_json()
        self.assertEqual(encoded, record.to_json())
        self.assertEqual(api.ParsingEvaluationRecord.from_json(encoded), record)

        duplicate = deterministic[0]
        with self.assertRaises(ValueError):
            api.ParsingEvaluationRecord(
                1,
                self.identity(api),
                self.target(api),
                2,
                (duplicate, duplicate),
                (),
                (),
                api.EfficiencyMetrics(),
                (),
            )

    def test_protocol_layer_is_vendor_neutral_and_has_no_side_effect_runtime(self) -> None:
        self.api()
        source = PROTOCOL_MODULE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "langfuse",
            "langgraph",
            "langchain",
            "subprocess",
            "requests",
            "write_text(",
            "write_bytes(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
