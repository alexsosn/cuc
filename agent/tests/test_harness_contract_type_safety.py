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


if __name__ == "__main__":
    unittest.main()
