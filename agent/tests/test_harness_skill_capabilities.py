from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEW_SKILL = REPO_ROOT / ".agents" / "skills" / "review-automatic-parsing" / "SKILL.md"
REGEN_SKILL = REPO_ROOT / ".agents" / "skills" / "regenerate-automatic-parsing" / "SKILL.md"


class SkillCapabilityContractTest(unittest.TestCase):
    maxDiff = None

    def api(self):
        try:
            return importlib.import_module("harness.skill_capabilities")
        except ModuleNotFoundError as exc:
            self.fail(f"HARN-008 capability module is not implemented yet: {exc}")

    def registry(self):
        return self.api().SkillCapabilityRegistry(REPO_ROOT)

    def test_manifest_contract_is_framework_neutral_and_strict(self) -> None:
        api = self.api()
        payload = {
            "schema_version": 1,
            "contract_version": "1.0.0",
            "canonical_name": "example-skill",
            "skill_path": ".agents/skills/example-skill",
            "aliases": [".claude/skills/example-skill"],
            "work_unit": "tablet",
            "ordered_stages": ["inspect", "verify"],
            "scope_invariants": ["complete-tablet"],
            "required_evidence": ["primary-source"],
            "optional_evidence": [],
            "authoritative_resources": ["agent/prompts/example.md"],
            "helper_resources": [".agents/skills/example-skill/scripts/check.py"],
            "effect": "read-only",
            "write_scopes": [],
            "read_only_scopes": ["reviewed/**"],
            "completion_verifiers": ["check-complete"],
            "escalation_targets": ["human-review"],
            "evaluator_requirements": ["lint"],
            "permissions": ["repository-read"],
        }
        manifest = api.SkillCapabilityManifest.from_dict(payload)
        self.assertEqual(manifest.to_dict(), payload)
        self.assertEqual(manifest.effect, api.SkillEffect.READ_ONLY)

        with self.assertRaises(ValueError):
            api.SkillCapabilityManifest.from_dict({**payload, "schema_version": 2})
        with self.assertRaises(ValueError):
            api.SkillCapabilityManifest.from_dict({**payload, "langgraph": {"node": "review"}})
        with self.assertRaises(ValueError):
            api.SkillCapabilityManifest.from_dict({**payload, "ordered_stages": "inspect"})
        with self.assertRaises(ValueError):
            api.SkillCapabilityManifest.from_dict({**payload, "skill_path": "../outside"})
        with self.assertRaises(ValueError):
            api.SkillCapabilityManifest.from_dict({**payload, "aliases": [".agents/skills/other"]})

    def test_review_skill_preserves_complete_column_every_token_semantics(self) -> None:
        api = self.api()
        manifest = self.registry().get("review-automatic-parsing")

        self.assertEqual(manifest.work_unit, "column")
        self.assertEqual(manifest.effect, api.SkillEffect.CURATED_DATA_WRITE)
        self.assertEqual(manifest.write_scopes, ("reviewed/**",))
        self.assertEqual(
            manifest.ordered_stages,
            (
                "establish-scope",
                "seed-if-needed",
                "build-worklist",
                "review-each-token",
                "verify",
                "report",
            ),
        )
        self.assertTrue(
            {
                "complete-column",
                "every-token-in-order",
                "worklists-attention-only",
                "preserve-defensible-ambiguity",
                "auto-parsing-generated-never-hand-edit",
                "one-column-per-review-commit",
            }.issubset(set(manifest.scope_invariants))
        )
        self.assertIn("dulat", manifest.required_evidence)
        self.assertIn("tropper", manifest.required_evidence)
        self.assertIn("burns-cultic-vocabulary", manifest.required_evidence)
        self.assertEqual(manifest.optional_evidence, ())
        self.assertIn(
            "agent/prompts/Morphological_Labeling_Agent_Guide.md",
            manifest.authoritative_resources,
        )
        self.assertIn(
            ".agents/skills/review-automatic-parsing/scripts/review_status.py",
            manifest.helper_resources,
        )

        skill_text = REVIEW_SKILL.read_text(encoding="utf-8")
        normalized_skill_text = " ".join(skill_text.split())
        self.assertIn("bounded by a column", skill_text)
        self.assertIn("**Every token, in order.**", skill_text)
        self.assertIn(
            "the worklist tells you where to look hardest, not where to stop",
            normalized_skill_text,
        )
        self.assertIn("`auto_parsing/**` is\n   generated: never hand-edit it", skill_text)

    def test_regeneration_skill_maps_as_distinct_generated_data_workflow(self) -> None:
        api = self.api()
        manifest = self.registry().get("regenerate-automatic-parsing")

        self.assertEqual(manifest.work_unit, "regeneration-scope")
        self.assertEqual(manifest.effect, api.SkillEffect.GENERATED_DATA_WRITE)
        self.assertIn("auto_parsing/**", manifest.write_scopes)
        self.assertIn("reviewed/**", manifest.read_only_scopes)
        self.assertEqual(
            manifest.ordered_stages,
            (
                "choose-route",
                "snapshot-baseline",
                "dry-run",
                "regenerate-to-stage",
                "validate",
                "publish",
            ),
        )
        self.assertTrue(
            {
                "generated-output-only",
                "reviewed-read-only",
                "dry-run-before-write",
                "stage-before-publish",
                "step-change-safeguard",
                "failed-stage-not-resumable",
            }.issubset(set(manifest.scope_invariants))
        )
        self.assertIn(
            ".agents/skills/regenerate-automatic-parsing/references/version-routing.md",
            manifest.authoritative_resources,
        )

        skill_text = REGEN_SKILL.read_text(encoding="utf-8")
        self.assertIn("Start with a dry run", skill_text)
        self.assertIn("empty staging output directory", skill_text)
        self.assertIn("Confirm reviewed TSVs are unchanged", skill_text)

    def test_aliases_canonicalize_and_legacy_only_skill_is_not_silently_promoted(self) -> None:
        api = self.api()
        registry = self.registry()
        canonical = registry.get("review-automatic-parsing")
        via_alias = registry.resolve_alias(".claude/skills/review-automatic-parsing")

        self.assertIs(canonical, via_alias)
        self.assertEqual(
            registry.unmanaged_legacy_paths,
            (".claude/skills/parse-ugaritic-passive-participle",),
        )
        with self.assertRaises(api.UnmanagedSkillError):
            registry.get("parse-ugaritic-passive-participle")

    def test_provenance_hashes_manifest_skill_package_and_declared_resources(self) -> None:
        registry = self.registry()
        provenance = registry.provenance("review-automatic-parsing")
        manifest = registry.get("review-automatic-parsing")

        self.assertEqual(provenance.canonical_name, manifest.canonical_name)
        self.assertEqual(provenance.contract_version, manifest.contract_version)
        self.assertRegex(provenance.manifest_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(provenance.skill_package_sha256, r"^[0-9a-f]{64}$")
        digests = {item.path: item.sha256 for item in provenance.resources}
        expected_paths = {
            f"{manifest.skill_path}/SKILL.md",
            *manifest.authoritative_resources,
            *manifest.helper_resources,
        }
        self.assertEqual(set(digests), expected_paths)
        self.assertTrue(all(len(value) == 64 for value in digests.values()))
        self.assertEqual(
            registry.provenance("review-automatic-parsing"),
            registry.provenance("review-automatic-parsing"),
        )

    def test_provenance_detects_declared_and_undeclared_package_drift_without_git(self) -> None:
        api = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / ".agents" / "skills" / "example-skill"
            manifest_dir = root / "agent" / "harness" / "capability_manifests"
            skill_dir.mkdir(parents=True)
            manifest_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: example-skill\ndescription: Example capability.\n---\n\n# Example\n",
                encoding="utf-8",
            )
            (skill_dir / "reference.md").write_text("first\n", encoding="utf-8")
            (skill_dir / "unlisted-helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            payload = {
                "schema_version": 1,
                "contract_version": "1.0.0",
                "canonical_name": "example-skill",
                "skill_path": ".agents/skills/example-skill",
                "aliases": [],
                "work_unit": "tablet",
                "ordered_stages": ["inspect"],
                "scope_invariants": ["complete-tablet"],
                "required_evidence": [],
                "optional_evidence": [],
                "authoritative_resources": [
                    ".agents/skills/example-skill/reference.md"
                ],
                "helper_resources": [],
                "effect": "read-only",
                "write_scopes": [],
                "read_only_scopes": [],
                "completion_verifiers": ["done"],
                "escalation_targets": [],
                "evaluator_requirements": [],
                "permissions": ["repository-read"],
            }
            (manifest_dir / "example-skill.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

            registry = api.SkillCapabilityRegistry(root)
            first = registry.provenance("example-skill")

            (skill_dir / "reference.md").write_text("second\n", encoding="utf-8")
            declared_changed = registry.provenance("example-skill")
            self.assertNotEqual(first, declared_changed)

            (skill_dir / "reference.md").write_text("first\n", encoding="utf-8")
            (skill_dir / "unlisted-helper.py").write_text("VALUE = 2\n", encoding="utf-8")
            package_changed = registry.provenance("example-skill")
            self.assertNotEqual(first, package_changed)
            self.assertNotEqual(
                first.skill_package_sha256,
                package_changed.skill_package_sha256,
            )


if __name__ == "__main__":
    unittest.main()
