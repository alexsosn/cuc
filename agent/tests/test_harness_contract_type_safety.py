import unittest

from harness import contracts as c
from harness import state_machine as sm


class HarnessContractTypeSafetyTests(unittest.TestCase):
    def task(self):
        return c.TaskSpec(
            "HARN-002",
            "Harness contracts",
            "Reject malformed durable state",
            ("serializable",),
        )

    def research(self):
        return c.ResearchArtifact("research-1", "researched")

    def plan(self):
        return c.PlanArtifact("plan-1", "planned", ("test", "implement"))

    def intent(self):
        return c.TestIntent(
            "targeted",
            c.TestKind.TARGETED,
            ("python", "-m", "pytest"),
            "agent",
            "contract tests",
        )

    def change(self):
        return c.ChangeSet(
            "change-1",
            "implement contracts",
            ("agent/harness/contracts.py",),
            ("op-1",),
        )

    def test_run_state_rejects_non_contract_nested_values(self):
        cases = (
            dict(run_id="bad-task", task={"task_id": "not-a-contract"}),
            dict(
                run_id="bad-research",
                task=self.task(),
                phase=c.RunPhase.PLAN,
                research={"artifact_id": "not-a-contract"},
            ),
            dict(
                run_id="bad-plan",
                task=self.task(),
                phase=c.RunPhase.TEST_DESIGN,
                research=self.research(),
                plan={"plan_id": "not-a-contract"},
            ),
            dict(
                run_id="bad-intent",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=({"intent_id": "not-a-contract"},),
            ),
            dict(
                run_id="bad-change",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=(self.intent(),),
                changes=({"change_id": "not-a-contract"},),
            ),
            dict(
                run_id="bad-test-result",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=(self.intent(),),
                changes=(self.change(),),
                test_results=({"intent_id": "not-a-contract"},),
            ),
            dict(
                run_id="bad-eval-result",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=(self.intent(),),
                changes=(self.change(),),
                eval_results=({"eval_id": "not-a-contract"},),
            ),
            dict(
                run_id="bad-review",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=(self.intent(),),
                review={"disposition": "request-changes"},
            ),
        )
        for payload in cases:
            with self.subTest(run_id=payload["run_id"]):
                with self.assertRaises(ValueError):
                    c.RunState(**payload)

    def test_reducer_cannot_admit_wrong_typed_artifact(self):
        state = c.RunState(run_id="run", task=self.task())
        with self.assertRaises(ValueError):
            sm.apply_event(
                state,
                sm.ResearchRecorded({"artifact_id": "not-a-contract"}),
            )

    def test_scalar_strings_are_not_tuple_like_contract_values(self):
        with self.assertRaises(ValueError):
            c.TaskSpec("task", "title", "objective", "criterion")
        with self.assertRaises(ValueError):
            c.PlanArtifact("plan", "summary", "step")
        with self.assertRaises(ValueError):
            c.TestIntent(
                "intent",
                c.TestKind.TARGETED,
                "pytest",
                "agent",
                "scalar command",
            )
        with self.assertRaises(ValueError):
            c.ChangeSet("change", "summary", "agent/harness/contracts.py")

    def test_deserialization_cannot_turn_scalar_strings_into_tuples(self):
        malformed = (
            lambda: c.TaskSpec.from_dict(
                {
                    "task_id": "task",
                    "title": "title",
                    "objective": "objective",
                    "acceptance_criteria": "criterion",
                }
            ),
            lambda: c.PlanArtifact.from_dict(
                {
                    "plan_id": "plan",
                    "summary": "summary",
                    "steps": "step",
                }
            ),
            lambda: c.TestIntent.from_dict(
                {
                    "intent_id": "intent",
                    "kind": c.TestKind.TARGETED.value,
                    "command": "pytest",
                    "working_directory": "agent",
                    "description": "scalar command",
                }
            ),
            lambda: c.ChangeSet.from_dict(
                {
                    "change_id": "change",
                    "summary": "summary",
                    "changed_paths": "agent/harness/contracts.py",
                }
            ),
        )
        for load in malformed:
            with self.subTest(loader=load):
                with self.assertRaises(ValueError):
                    load()

    def test_implement_checkpoint_with_change_requires_retry_cause(self):
        with self.assertRaises(ValueError):
            c.RunState(
                run_id="skipped-verification",
                task=self.task(),
                phase=c.RunPhase.IMPLEMENT,
                research=self.research(),
                plan=self.plan(),
                test_intents=(self.intent(),),
                changes=(self.change(),),
            )

    def test_transition_events_reject_wrong_contract_payloads(self):
        malformed = (
            lambda: sm.ResearchRecorded({}),
            lambda: sm.PlanRecorded({}),
            lambda: sm.TestsDeclared(({},)),
            lambda: sm.ChangeRecorded({}),
            lambda: sm.TestRecorded({}),
            lambda: sm.EvalRecorded({}),
            lambda: sm.ReviewRecorded({}),
        )
        for build in malformed:
            with self.subTest(builder=build):
                with self.assertRaises(ValueError):
                    build()

    def test_reducer_rejects_non_run_state(self):
        with self.assertRaises(ValueError):
            sm.apply_event({}, sm.ResumeRequested())


if __name__ == "__main__":
    unittest.main()
