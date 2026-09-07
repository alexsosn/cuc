from __future__ import annotations

import importlib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COLUMN_STATE_MODULE = REPO_ROOT / "agent" / "harness" / "column_state.py"


class ColumnRunStateContractTest(unittest.TestCase):
    maxDiff = None

    def api(self):
        try:
            return importlib.import_module("harness.column_state")
        except ModuleNotFoundError as exc:
            self.fail(f"HARN-018 column state is not implemented yet: {exc}")

    def fixture(self):
        api = self.api()
        capability = api.CapabilityRef(
            canonical_name="review-automatic-parsing",
            contract_version="1.0.0",
            provenance_sha256="a" * 64,
        )
        task = api.ColumnTask(
            task_id="column-run-1",
            corpus="CUC",
            tablet="KTU 1.5",
            column="I",
            repository_revision="165c34dc",
            capability=capability,
            required_completion_gates=(
                "review-status-clean",
                "lint-error-delta-no-regression",
                "report-token-count",
            ),
            evidence_priority_token_ids=("t2",),
        )
        snapshot = api.ColumnSnapshot(
            snapshot_id="snapshot-1",
            source_ref="auto_parsing/0.2.8/KTU 1.5.tsv",
            source_provenance="sha256:source-snapshot",
            tokens=(
                api.ColumnToken("t1", 1, "1.5:I:1", "a"),
                api.ColumnToken("t2", 2, "1.5:I:2", "b"),
                api.ColumnToken("t3", 3, "1.5:I:3", "c"),
            ),
        )
        state = api.ColumnRunState.initial(task, snapshot)
        evidence = api.EvidenceRecord(
            evidence_id="ev-main",
            source_id="dulat",
            source_ref="DULAT entry / line evidence",
            provenance_ref="dulat-cache:sha256:1234",
            summary="Lexical and morphological evidence.",
        )
        state = api.apply_column_event(
            state,
            api.EvidenceRecorded("event-evidence-main", evidence),
        )
        return api, state

    def decision(
        self,
        api,
        decision_id: str,
        token_id: str,
        *,
        analyses: tuple[str, ...] | None = None,
        summary: str = "Reviewed against evidence.",
        revisit_of: str | None = None,
    ):
        return api.TokenDecision(
            decision_id=decision_id,
            token_id=token_id,
            analyses=analyses or (f"analysis-{token_id}",),
            evidence_ids=("ev-main",),
            summary=summary,
            revisit_of=revisit_of,
        )

    def review_initial_column(self, api, state):
        events = []
        for index, token_id in enumerate(("t1", "t2", "t3"), start=1):
            event = api.TokenReviewed(
                f"event-review-{token_id}",
                self.decision(api, f"d{index}", token_id),
            )
            state = api.apply_column_event(state, event)
            events.append(event)
        return state, tuple(events)

    def close_reconciliation(self, api, state, event_id: str = "event-reconcile-close"):
        return api.apply_column_event(state, api.ReconciliationClosed(event_id))

    def record_required_gates(self, api, state, *, revision: int | None = None, prefix: str = "gate"):
        revision = state.decision_revision if revision is None else revision
        for gate_id in state.task.required_completion_gates:
            result = api.CompletionGateResult(
                gate_id=gate_id,
                passed=True,
                decision_revision=revision,
                evidence_refs=(f"ci:{gate_id}:{revision}",),
                summary="Gate passed.",
            )
            state = api.apply_column_event(
                state,
                api.CompletionGateRecorded(f"event-{prefix}-{gate_id}", result),
            )
        return state

    def test_complete_column_scope_and_priority_hints_do_not_change_traversal(self) -> None:
        api, state = self.fixture()

        self.assertEqual(state.next_token_id, "t1")
        self.assertEqual(state.task.evidence_priority_token_ids, ("t2",))
        self.assertFalse(state.initial_pass_complete)

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(
                state,
                api.TokenReviewed(
                    "event-out-of-order",
                    self.decision(api, "d2", "t2"),
                ),
            )

        state, _ = self.review_initial_column(api, state)
        self.assertTrue(state.initial_pass_complete)
        self.assertIsNone(state.next_token_id)
        self.assertEqual(state.cursor.next_index, 3)
        self.assertEqual(
            tuple(decision.token_id for decision in state.initial_decisions),
            ("t1", "t2", "t3"),
        )

    def test_skipped_token_cannot_complete(self) -> None:
        api, state = self.fixture()
        state = api.apply_column_event(
            state,
            api.TokenReviewed("event-review-t1", self.decision(api, "d1", "t1")),
        )

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(state, api.ColumnCompleted("event-complete"))

    def test_resume_cursor_and_event_replay_are_stable_and_conflict_safe(self) -> None:
        api, state = self.fixture()
        event = api.TokenReviewed(
            "event-review-t1",
            self.decision(api, "d1", "t1"),
        )
        state = api.apply_column_event(state, event)
        encoded = state.to_json()

        self.assertEqual(encoded, state.to_json())
        resumed = api.ColumnRunState.from_json(encoded)
        self.assertEqual(resumed, state)
        self.assertEqual(resumed.next_token_id, "t2")

        replayed = api.apply_column_event(resumed, event)
        self.assertEqual(replayed, resumed)
        self.assertEqual(replayed.cursor.next_index, 1)
        self.assertEqual(len(replayed.decisions), 1)

        conflicting = api.TokenReviewed(
            "event-review-t1",
            self.decision(api, "different-decision", "t1", summary="conflict"),
        )
        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(resumed, conflicting)

    def test_evidence_provenance_and_alternative_readings_survive_round_trip(self) -> None:
        api, state = self.fixture()
        with self.assertRaises(ValueError):
            api.EvidenceRecord(
                evidence_id="bad",
                source_id="dulat",
                source_ref="entry",
                provenance_ref="",
                summary="Missing provenance.",
            )

        alternatives = ("vb G impv. m. sg.", "n m. sg. cstr.")
        state = api.apply_column_event(
            state,
            api.TokenReviewed(
                "event-review-t1",
                self.decision(api, "d1", "t1", analyses=alternatives),
            ),
        )
        round_tripped = api.ColumnRunState.from_json(state.to_json())
        decision = round_tripped.latest_decision("t1")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.analyses, alternatives)
        self.assertEqual(decision.evidence_ids, ("ev-main",))
        self.assertEqual(round_tripped.evidence[0].provenance_ref, "dulat-cache:sha256:1234")

    def test_revisit_requires_explicit_request_and_preserves_history(self) -> None:
        api, state = self.fixture()
        state, _ = self.review_initial_column(api, state)

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(
                state,
                api.TokenRevisited(
                    "event-revisit-without-request",
                    "missing-request",
                    self.decision(api, "d4", "t2", revisit_of="d2"),
                ),
            )

        request = api.RevisitRequest(
            request_id="revisit-t2",
            token_id="t2",
            reason="Corpus parallel requires a second look.",
        )
        state = api.apply_column_event(
            state,
            api.RevisitRequested("event-request-revisit-t2", request),
        )
        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-revisit-t2",
                "revisit-t2",
                self.decision(
                    api,
                    "d4",
                    "t2",
                    analyses=("revised-analysis", "preserved-alternative"),
                    revisit_of="d2",
                ),
            ),
        )

        history = state.decision_history("t2")
        self.assertEqual(tuple(item.decision_id for item in history), ("d2", "d4"))
        self.assertEqual(state.resolved_revisit_request_ids, ("revisit-t2",))
        self.assertEqual(state.decision_revision, 4)

    def test_required_reconciliation_finding_blocks_close_until_explicit_revisit(self) -> None:
        api, state = self.fixture()
        state, _ = self.review_initial_column(api, state)
        finding = api.CorpusReconciliationFinding(
            finding_id="finding-t2",
            scope=api.ReconciliationScope.CORPUS,
            token_ids=("t2",),
            evidence_ids=("ev-main",),
            summary="Parallel evidence conflicts with the initial decision.",
            requires_revisit=True,
        )
        state = api.apply_column_event(
            state,
            api.ReconciliationFindingRecorded("event-finding-t2", finding),
        )

        with self.assertRaises(api.InvalidColumnTransition):
            self.close_reconciliation(api, state)

        request = api.RevisitRequest(
            request_id="revisit-finding-t2",
            token_id="t2",
            reason="Resolve required corpus reconciliation finding.",
            finding_id="finding-t2",
        )
        state = api.apply_column_event(
            state,
            api.RevisitRequested("event-request-finding-t2", request),
        )
        with self.assertRaises(api.InvalidColumnTransition):
            self.close_reconciliation(api, state, "event-close-too-early")

        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-resolve-finding-t2",
                "revisit-finding-t2",
                self.decision(api, "d4", "t2", revisit_of="d2"),
            ),
        )
        state = self.close_reconciliation(api, state, "event-close-after-revisit")
        self.assertTrue(state.reconciliation_closed)

    def test_completion_requires_fresh_successful_gates_and_revisit_invalidates_old_gates(self) -> None:
        api, state = self.fixture()
        state, _ = self.review_initial_column(api, state)
        state = self.close_reconciliation(api, state)
        state = self.record_required_gates(api, state, revision=3, prefix="initial")

        request = api.RevisitRequest(
            request_id="late-revisit-t2",
            token_id="t2",
            reason="Late column reconciliation correction.",
        )
        state = api.apply_column_event(
            state,
            api.RevisitRequested("event-late-request", request),
        )
        state = api.apply_column_event(
            state,
            api.TokenRevisited(
                "event-late-revisit",
                "late-revisit-t2",
                self.decision(api, "d4", "t2", revisit_of="d2"),
            ),
        )
        self.assertEqual(state.decision_revision, 4)
        self.assertFalse(state.reconciliation_closed)

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(state, api.ColumnCompleted("event-complete-stale"))

        state = self.close_reconciliation(api, state, "event-reclose")
        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(state, api.ColumnCompleted("event-complete-old-gates"))

        state = self.record_required_gates(api, state, revision=4, prefix="fresh")
        completed = api.apply_column_event(state, api.ColumnCompleted("event-complete-fresh"))
        self.assertIsNotNone(completed.completion)
        self.assertEqual(completed.completion.decision_revision, 4)
        self.assertEqual(
            completed.completion.gate_ids,
            completed.task.required_completion_gates,
        )

    def test_failed_required_gate_blocks_completion(self) -> None:
        api, state = self.fixture()
        state, _ = self.review_initial_column(api, state)
        state = self.close_reconciliation(api, state)
        for gate_id in state.task.required_completion_gates:
            result = api.CompletionGateResult(
                gate_id=gate_id,
                passed=gate_id != "lint-error-delta-no-regression",
                decision_revision=state.decision_revision,
                evidence_refs=(f"gate:{gate_id}",),
                summary="Observed gate result.",
            )
            state = api.apply_column_event(
                state,
                api.CompletionGateRecorded(f"event-result-{gate_id}", result),
            )

        with self.assertRaises(api.InvalidColumnTransition):
            api.apply_column_event(state, api.ColumnCompleted("event-complete-failed-gate"))

    def test_priority_hints_must_reference_snapshot_but_cannot_narrow_snapshot(self) -> None:
        api = self.api()
        capability = api.CapabilityRef(
            "review-automatic-parsing",
            "1.0.0",
            "b" * 64,
        )
        task = api.ColumnTask(
            "run-2",
            "CUC",
            "KTU 1.5",
            "II",
            "repo-sha",
            capability,
            ("review-status-clean",),
            ("not-in-snapshot",),
        )
        snapshot = api.ColumnSnapshot(
            "snapshot-2",
            "auto_parsing/example.tsv",
            "snapshot-provenance",
            (api.ColumnToken("t1", 1, "1:1", "x"),),
        )
        with self.assertRaises(ValueError):
            api.ColumnRunState.initial(task, snapshot)

    def test_contract_layer_has_no_framework_or_side_effect_runtime_imports(self) -> None:
        self.api()
        source = COLUMN_STATE_MODULE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "langgraph",
            "langchain",
            "langfuse",
            "subprocess",
            "requests",
            "github",
            "write_text(",
            "write_bytes(",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
