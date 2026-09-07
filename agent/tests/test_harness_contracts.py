import ast
import importlib
import json
import unittest
from pathlib import Path


class HarnessContractTests(unittest.TestCase):
    def contracts(self):
        try:
            return importlib.import_module("harness.contracts")
        except ModuleNotFoundError as exc:
            self.fail(f"harness contracts are not implemented yet: {exc}")

    def state_machine(self):
        try:
            return importlib.import_module("harness.state_machine")
        except ModuleNotFoundError as exc:
            self.fail(f"harness state machine is not implemented yet: {exc}")

    def task(self):
        c = self.contracts()
        return c.TaskSpec(
            task_id="HARN-002",
            title="Harness contracts",
            objective="Define durable framework-neutral state",
            acceptance_criteria=("serializable", "validated"),
        )

    def intent(self, intent_id="targeted", kind=None):
        c = self.contracts()
        return c.TestIntent(
            intent_id=intent_id,
            kind=kind or c.TestKind.TARGETED,
            command=("python", "-m", "pytest", "tests/test_harness_contracts.py", "-q"),
            working_directory="agent",
            description="contract tests",
        )

    def change(self, change_id="change-1", operation_ids=("op-1",)):
        c = self.contracts()
        return c.ChangeSet(
            change_id=change_id,
            summary="implement contracts",
            changed_paths=("agent/harness/contracts.py",),
            operation_ids=operation_ids,
        )

    def successful_test(self, change_id="change-1", intent_id="targeted", head="head-1"):
        c = self.contracts()
        return c.TestResult(
            intent_id=intent_id,
            change_id=change_id,
            outcome=c.GateOutcome.SUCCESS,
            head_sha=head,
            executed_sha="merge-1",
            exit_code=0,
            passed_tests=1,
            failed_tests=0,
            summary="passed",
        )

    def state_at_verify(self):
        c = self.contracts()
        sm = self.state_machine()
        state = c.RunState(run_id="run-1", task=self.task())
        state = sm.apply_event(
            state,
            sm.ResearchRecorded(c.ResearchArtifact("research-1", "inventory complete")),
        )
        state = sm.apply_event(
            state,
            sm.PlanRecorded(c.PlanArtifact("plan-1", "contract plan", ("test", "implement"))),
        )
        state = sm.apply_event(state, sm.TestsDeclared((self.intent(),)))
        state = sm.apply_event(state, sm.ChangeRecorded(self.change()))
        return state

    def test_required_public_surface_exists(self):
        c = self.contracts()
        sm = self.state_machine()
        for name in (
            "TaskSpec",
            "ResearchArtifact",
            "PlanArtifact",
            "TestIntent",
            "TestResult",
            "EvalResult",
            "ChangeSet",
            "ReviewFinding",
            "ReviewResult",
            "RunState",
            "RunPhase",
            "GateOutcome",
            "TestKind",
            "FindingSeverity",
            "ReviewDisposition",
        ):
            self.assertTrue(hasattr(c, name), name)
        for name in (
            "InvalidTransition",
            "ResearchRecorded",
            "PlanRecorded",
            "TestsDeclared",
            "ChangeRecorded",
            "TestRecorded",
            "EvalRecorded",
            "VerificationPassed",
            "ReviewRecorded",
            "ResumeRequested",
            "apply_event",
        ):
            self.assertTrue(hasattr(sm, name), name)

    def test_contract_modules_have_no_framework_vendor_imports(self):
        forbidden = {"langgraph", "langchain", "langfuse", "deepagents", "pydantic"}
        for module in (self.contracts(), self.state_machine()):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imported_roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".")[0])
            self.assertTrue(forbidden.isdisjoint(imported_roots), imported_roots & forbidden)

    def test_task_and_plan_validate_required_content(self):
        c = self.contracts()
        with self.assertRaises(ValueError):
            c.TaskSpec("", "title", "objective", ("criterion",))
        with self.assertRaises(ValueError):
            c.TaskSpec("task", "title", "objective", ())
        with self.assertRaises(ValueError):
            c.PlanArtifact("plan", "summary", ())
        with self.assertRaises(ValueError):
            c.ResearchArtifact("research", "   ")

    def test_test_intent_is_structured_and_validated(self):
        c = self.contracts()
        intent = self.intent()
        self.assertEqual(intent.command[0], "python")
        with self.assertRaises(ValueError):
            c.TestIntent("x", c.TestKind.TARGETED, (), "agent", "empty command")
        with self.assertRaises(ValueError):
            c.TestIntent("x", c.TestKind.TARGETED, ("pytest",), "", "missing cwd")

    def test_test_result_validates_execution_identity_and_outcome(self):
        c = self.contracts()
        with self.assertRaises(ValueError):
            c.TestResult(
                "i",
                "c",
                c.GateOutcome.SUCCESS,
                "head",
                None,
                0,
                1,
                0,
                "passed without execution identity",
            )
        with self.assertRaises(ValueError):
            c.TestResult(
                "i", "c", c.GateOutcome.SUCCESS, "head", "merge", 1, 0, 1, "not success"
            )
        with self.assertRaises(ValueError):
            c.TestResult(
                "i", "c", c.GateOutcome.TEST_FAILURE, "head", "merge", 0, 1, 0, "contradiction"
            )
        with self.assertRaises(ValueError):
            c.TestResult(
                "i",
                "c",
                c.GateOutcome.BLOCKED_EXECUTION,
                "head",
                None,
                None,
                1,
                0,
                "claimed pass while blocked",
            )
        blocked = c.TestResult(
            "i",
            "c",
            c.GateOutcome.BLOCKED_EXECUTION,
            "head",
            None,
            None,
            0,
            0,
            "runner unavailable",
        )
        self.assertEqual(blocked.outcome, c.GateOutcome.BLOCKED_EXECUTION)

    def test_review_contract_rejects_impossible_dispositions(self):
        c = self.contracts()
        with self.assertRaises(ValueError):
            c.ReviewFinding(
                "f-1", c.FindingSeverity.INFO, "informational blocker", (), True
            )
        blocker = c.ReviewFinding(
            "f-2", c.FindingSeverity.MAJOR, "resume can duplicate write", ("test:1",), True
        )
        with self.assertRaises(ValueError):
            c.ReviewResult(
                "r-1",
                "reviewer",
                "context-1",
                "head-1",
                c.ReviewDisposition.APPROVE,
                "approved despite blocker",
                (blocker,),
            )
        with self.assertRaises(ValueError):
            c.ReviewResult(
                "r-2",
                "reviewer",
                "context-2",
                "head-1",
                c.ReviewDisposition.REQUEST_CHANGES,
                "no blocking finding",
                (),
            )

    def test_operation_ids_are_unique_within_and_across_changes(self):
        c = self.contracts()
        with self.assertRaises(ValueError):
            self.change(operation_ids=("op-1", "op-1"))
        first = self.change("change-1", ("op-1",))
        second = self.change("change-2", ("op-1",))
        with self.assertRaises(ValueError):
            c.RunState(run_id="run", task=self.task(), changes=(first, second))

    def test_run_state_json_round_trip_is_lossless(self):
        c = self.contracts()
        finding = c.ReviewFinding(
            "f-1", c.FindingSeverity.MINOR, "document retry caveat", ("doc:1",), False
        )
        state = c.RunState(
            run_id="run-roundtrip",
            task=self.task(),
            phase=c.RunPhase.REVIEW,
            research=c.ResearchArtifact("research-1", "researched", ("repo:file",)),
            plan=c.PlanArtifact("plan-1", "planned", ("test", "implement")),
            test_intents=(self.intent(),),
            changes=(self.change(),),
            test_results=(self.successful_test(),),
            eval_results=(
                c.EvalResult(
                    "eval-1",
                    "change-1",
                    c.GateOutcome.SUCCESS,
                    "head-1",
                    "merge-1",
                    "no regression",
                    (("macro_f1", 0.95), ("coverage", True)),
                    ("artifact:score",),
                ),
            ),
            review=c.ReviewResult(
                "review-1",
                "independent-reviewer",
                "fresh-context",
                "head-1",
                c.ReviewDisposition.APPROVE,
                "approved",
                (finding,),
            ),
            verified_head_sha="head-1",
        )
        payload = state.to_dict()
        encoded = json.dumps(payload, sort_keys=True)
        restored = c.RunState.from_dict(json.loads(encoded))
        self.assertEqual(restored, state)
        self.assertIsInstance(restored.test_intents, tuple)
        self.assertEqual(dict(restored.eval_results[0].metrics)["macro_f1"], 0.95)

    def test_happy_path_reaches_complete_without_side_effects(self):
        c = self.contracts()
        sm = self.state_machine()
        state = self.state_at_verify()
        state = sm.apply_event(state, sm.TestRecorded(self.successful_test()))
        self.assertEqual(state.phase, c.RunPhase.VERIFY)
        state = sm.apply_event(state, sm.VerificationPassed())
        self.assertEqual(state.phase, c.RunPhase.REVIEW)
        review = c.ReviewResult(
            "review-1",
            "reviewer",
            "independent-context",
            "head-1",
            c.ReviewDisposition.APPROVE,
            "no blocking findings",
            (),
        )
        state = sm.apply_event(state, sm.ReviewRecorded(review))
        self.assertEqual(state.phase, c.RunPhase.COMPLETE)

    def test_failed_test_and_regression_return_to_implementation(self):
        c = self.contracts()
        sm = self.state_machine()
        failed = c.TestResult(
            "targeted",
            "change-1",
            c.GateOutcome.TEST_FAILURE,
            "head-1",
            "merge-1",
            1,
            0,
            1,
            "assertion failed",
        )
        state = sm.apply_event(self.state_at_verify(), sm.TestRecorded(failed))
        self.assertEqual(state.phase, c.RunPhase.IMPLEMENT)

        state = self.state_at_verify()
        regression = c.EvalResult(
            "reviewed-morphology",
            "change-1",
            c.GateOutcome.REGRESSION,
            "head-1",
            "merge-1",
            "macro F1 dropped",
            (("macro_f1_delta", -0.02),),
        )
        state = sm.apply_event(state, sm.EvalRecorded(regression))
        self.assertEqual(state.phase, c.RunPhase.IMPLEMENT)

    def test_blocked_execution_resumes_only_to_captured_phase(self):
        c = self.contracts()
        sm = self.state_machine()
        blocked = c.TestResult(
            "targeted",
            "change-1",
            c.GateOutcome.BLOCKED_EXECUTION,
            "head-1",
            None,
            None,
            0,
            0,
            "runner unavailable",
        )
        state = sm.apply_event(self.state_at_verify(), sm.TestRecorded(blocked))
        self.assertEqual(state.phase, c.RunPhase.BLOCKED)
        self.assertEqual(state.resume_phase, c.RunPhase.VERIFY)
        self.assertEqual(state.pause_reason, "runner unavailable")
        state = sm.apply_event(state, sm.ResumeRequested())
        self.assertEqual(state.phase, c.RunPhase.VERIFY)
        self.assertIsNone(state.resume_phase)
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(state, sm.ResumeRequested())

    def test_human_escalation_is_not_encoded_as_test_failure(self):
        c = self.contracts()
        sm = self.state_machine()
        escalated = c.TestResult(
            "targeted",
            "change-1",
            c.GateOutcome.HUMAN_ESCALATION,
            "head-1",
            "merge-1",
            None,
            0,
            0,
            "requires curator decision",
        )
        state = sm.apply_event(self.state_at_verify(), sm.TestRecorded(escalated))
        self.assertEqual(state.phase, c.RunPhase.AWAITING_HUMAN)
        self.assertEqual(state.resume_phase, c.RunPhase.VERIFY)

    def test_verification_requires_fresh_success_for_every_declared_test(self):
        c = self.contracts()
        sm = self.state_machine()
        state = c.RunState(run_id="run", task=self.task())
        state = sm.apply_event(
            state, sm.ResearchRecorded(c.ResearchArtifact("r", "research complete"))
        )
        state = sm.apply_event(
            state, sm.PlanRecorded(c.PlanArtifact("p", "plan", ("tests", "code")))
        )
        state = sm.apply_event(
            state,
            sm.TestsDeclared(
                (
                    self.intent("targeted"),
                    self.intent("regression", c.TestKind.REGRESSION),
                )
            ),
        )
        state = sm.apply_event(state, sm.ChangeRecorded(self.change()))
        state = sm.apply_event(state, sm.TestRecorded(self.successful_test("change-1", "targeted")))
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(state, sm.VerificationPassed())
        state = sm.apply_event(
            state,
            sm.TestRecorded(self.successful_test("change-1", "regression")),
        )
        state = sm.apply_event(state, sm.VerificationPassed())
        self.assertEqual(state.phase, c.RunPhase.REVIEW)
        self.assertEqual(state.verified_head_sha, "head-1")

    def test_stale_result_and_stale_review_are_rejected(self):
        c = self.contracts()
        sm = self.state_machine()
        stale_result = self.successful_test(change_id="old-change")
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(self.state_at_verify(), sm.TestRecorded(stale_result))

        state = self.state_at_verify()
        state = sm.apply_event(state, sm.TestRecorded(self.successful_test()))
        state = sm.apply_event(state, sm.VerificationPassed())
        stale_review = c.ReviewResult(
            "review",
            "reviewer",
            "fresh-context",
            "older-head",
            c.ReviewDisposition.APPROVE,
            "looked at an older revision",
            (),
        )
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(state, sm.ReviewRecorded(stale_review))

    def test_review_rejection_loops_and_cannot_reuse_operation_id(self):
        c = self.contracts()
        sm = self.state_machine()
        state = self.state_at_verify()
        state = sm.apply_event(state, sm.TestRecorded(self.successful_test()))
        state = sm.apply_event(state, sm.VerificationPassed())
        blocker = c.ReviewFinding(
            "f", c.FindingSeverity.MAJOR, "retry duplicates effect", (), True
        )
        review = c.ReviewResult(
            "review",
            "reviewer",
            "independent-context",
            "head-1",
            c.ReviewDisposition.REQUEST_CHANGES,
            "must revise",
            (blocker,),
        )
        state = sm.apply_event(state, sm.ReviewRecorded(review))
        self.assertEqual(state.phase, c.RunPhase.IMPLEMENT)
        self.assertIsNone(state.verified_head_sha)
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(
                state,
                sm.ChangeRecorded(self.change("change-2", operation_ids=("op-1",))),
            )

    def test_illegal_transition_order_is_rejected(self):
        c = self.contracts()
        sm = self.state_machine()
        state = c.RunState(run_id="run", task=self.task())
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(
                state, sm.PlanRecorded(c.PlanArtifact("p", "premature plan", ("step",)))
            )
        with self.assertRaises(sm.InvalidTransition):
            sm.apply_event(state, sm.VerificationPassed())


if __name__ == "__main__":
    unittest.main()
