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


if __name__ == "__main__":
    unittest.main()
