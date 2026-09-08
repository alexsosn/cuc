from __future__ import annotations

import importlib
import unittest


class ColumnStateAdversarialFindingTest(unittest.TestCase):
    def api(self):
        return importlib.import_module("harness.column_state")

    def unresolved_revisit_state(self):
        api = self.api()
        task = api.ColumnTask(
            "review-forgery",
            "CUC",
            "KTU 1.5",
            "I",
            "head-sha",
            api.CapabilityRef(
                "review-automatic-parsing",
                "1.0.0",
                "c" * 64,
            ),
            ("review-status-clean",),
            (),
        )
        snapshot = api.ColumnSnapshot(
            "snapshot-forgery",
            "auto_parsing/example.tsv",
            "snapshot:sha256:example",
            (
                api.ColumnToken("t1", 1, "1.5:I:1", "a"),
                api.ColumnToken("t2", 2, "1.5:I:2", "b"),
            ),
        )
        state = api.ColumnRunState.initial(task, snapshot)
        evidence = api.EvidenceRecord(
            "ev1",
            "dulat",
            "entry",
            "dulat:sha256:example",
            "Evidence used by both decisions.",
        )
        state = api.apply_column_event(
            state,
            api.EvidenceRecorded("event-evidence", evidence),
        )
        state = api.apply_column_event(
            state,
            api.TokenReviewed(
                "event-t1",
                api.TokenDecision(
                    "d1",
                    "t1",
                    ("analysis-1",),
                    ("ev1",),
                    "Initial decision.",
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.TokenReviewed(
                "event-t2",
                api.TokenDecision(
                    "d2",
                    "t2",
                    ("analysis-2",),
                    ("ev1",),
                    "Initial decision.",
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.RevisitRequested(
                "event-request",
                api.RevisitRequest(
                    "r1",
                    "t1",
                    "Reconciliation requires an explicit second look.",
                ),
            ),
        )
        return api, state

    def test_deserialization_rejects_forged_resolved_revisit_without_decision(self) -> None:
        api, state = self.unresolved_revisit_state()
        payload = state.to_dict()
        payload["resolved_revisit_request_ids"] = ["r1"]

        with self.assertRaises(ValueError):
            api.ColumnRunState.from_dict(payload)

    def test_revisit_decision_persists_authorizing_request_identity(self) -> None:
        api, state = self.unresolved_revisit_state()
        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-revisit",
                "r1",
                api.TokenDecision(
                    "d3",
                    "t1",
                    ("analysis-1-revised",),
                    ("ev1",),
                    "Explicit revisit decision.",
                    revisit_of="d1",
                ),
            ),
        )

        decision = state.latest_decision("t1")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.revisit_request_id, "r1")

        restored = api.ColumnRunState.from_json(state.to_json())
        restored_decision = restored.latest_decision("t1")
        self.assertEqual(restored_decision.revisit_request_id, "r1")
        self.assertEqual(restored.resolved_revisit_request_ids, ("r1",))

    def test_required_multi_token_finding_needs_revisit_for_each_affected_token(self) -> None:
        api = self.api()
        task = api.ColumnTask(
            "review-multi-token-finding",
            "CUC",
            "KTU 1.5",
            "I",
            "head-sha",
            api.CapabilityRef(
                "review-automatic-parsing",
                "1.0.0",
                "d" * 64,
            ),
            ("review-status-clean",),
            (),
        )
        snapshot = api.ColumnSnapshot(
            "snapshot-multi-token",
            "auto_parsing/example.tsv",
            "snapshot:sha256:multi-token",
            (
                api.ColumnToken("t1", 1, "1.5:I:1", "a"),
                api.ColumnToken("t2", 2, "1.5:I:2", "b"),
            ),
        )
        state = api.ColumnRunState.initial(task, snapshot)
        state = api.apply_column_event(
            state,
            api.EvidenceRecorded(
                "event-multi-evidence",
                api.EvidenceRecord(
                    "ev1",
                    "corpus-parallel",
                    "parallel-lines",
                    "parallel-index:sha256:example",
                    "Parallel evidence affects both tokens.",
                ),
            ),
        )
        for decision_id, token_id in (("d1", "t1"), ("d2", "t2")):
            state = api.apply_column_event(
                state,
                api.TokenReviewed(
                    f"event-{token_id}",
                    api.TokenDecision(
                        decision_id,
                        token_id,
                        (f"analysis-{token_id}",),
                        ("ev1",),
                        "Initial decision.",
                    ),
                ),
            )

        state = api.apply_column_event(
            state,
            api.ReconciliationFindingRecorded(
                "event-finding",
                api.CorpusReconciliationFinding(
                    "finding-both",
                    api.ReconciliationScope.CORPUS,
                    ("t1", "t2"),
                    ("ev1",),
                    "The corpus parallel requires both affected tokens to be revisited.",
                    True,
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.RevisitRequested(
                "event-request-t1",
                api.RevisitRequest(
                    "r1",
                    "t1",
                    "Resolve the finding for t1.",
                    "finding-both",
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-revisit-t1",
                "r1",
                api.TokenDecision(
                    "d3",
                    "t1",
                    ("analysis-t1-revised",),
                    ("ev1",),
                    "Revisited t1.",
                    revisit_of="d1",
                ),
            ),
        )

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(state, api.ReconciliationClosed("event-close-after-only-t1"))

        state = api.apply_column_event(
            state,
            api.RevisitRequested(
                "event-request-t2",
                api.RevisitRequest(
                    "r2",
                    "t2",
                    "Resolve the finding for t2.",
                    "finding-both",
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-revisit-t2",
                "r2",
                api.TokenDecision(
                    "d4",
                    "t2",
                    ("analysis-t2-revised",),
                    ("ev1",),
                    "Revisited t2.",
                    revisit_of="d2",
                ),
            ),
        )
        state = api.apply_column_event(
            state,
            api.ReconciliationClosed("event-close-after-both"),
        )
        self.assertTrue(state.reconciliation_closed)


if __name__ == "__main__":
    unittest.main()
