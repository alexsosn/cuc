from __future__ import annotations

import importlib
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_MODULE = REPO_ROOT / "agent" / "harness" / "_parsing_evaluation_core.py"


class ParsingEvaluationFinalReviewTest(unittest.TestCase):
    def api(self):
        return importlib.import_module("harness.parsing_evaluation")

    def workload(self, api):
        return api.ParsingWorkloadRef(
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

    def identity(self, api, model_id: str):
        return api.ParsingRunIdentity(
            f"run-{model_id}",
            self.workload(api),
            "provider",
            model_id,
            "version-1",
            "6" * 64,
        )

    def target(self, api):
        return api.EvaluationTarget(
            "target-1",
            "reviewed/KTU 1.5.tsv#column-I",
            "git-blob:reviewed-a",
            "score_reviewed_morphology.py",
            "git-blob:scorer-a",
            "7" * 64,
        )

    def record(self, api, *, model_id: str, decision_revision: int):
        return api.ParsingEvaluationRecord(
            schema_version=1,
            identity=self.identity(api, model_id),
            target=self.target(api),
            decision_revision=decision_revision,
            deterministic_measurements=(),
            supplementary_measurements=(),
            expert_feedback=(),
            efficiency=api.EfficiencyMetrics(),
            artifact_refs=(),
        )

    def test_backend_runs_remain_comparable_when_only_outcome_revision_differs(self) -> None:
        api = self.api()
        one_pass = self.record(api, model_id="model-a", decision_revision=5)
        revisited = self.record(api, model_id="model-b", decision_revision=8)

        report = api.compare_evaluation_records(
            one_pass,
            revisited,
            ignore_model_identity=True,
        )

        self.assertTrue(report.comparable)
        self.assertEqual(report.mismatched_dimensions, ())

    def test_private_core_remains_vendor_neutral(self) -> None:
        source = CORE_MODULE.read_text(encoding="utf-8").lower()
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
