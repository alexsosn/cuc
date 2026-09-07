# HARN-008 — Formalize existing skills as versioned harness capabilities

## Research question

How can the harness consume the workflows already encoded in `.agents/skills/**` without copying their scholarly prose into framework-specific prompts or inventing a second source of truth?

## Observed repository structure

- `.agents/skills/` is the canonical skill root. It currently contains 11 top-level skill packages.
- `.claude/skills/` is primarily an alias layer: e.g. `.claude/skills/review-automatic-parsing` is a symlink to `../../.agents/skills/review-automatic-parsing`.
- `parse-ugaritic-passive-participle` is the exception: it exists as a real package only under `.claude/skills/` and has no canonical `.agents/skills/` package.
- `review-automatic-parsing` is orchestration-heavy and owns the real agentic parsing workflow. Its current `SKILL.md` explicitly requires a complete column as the work/context unit, every token in textual order, worklists as attention aids only, whole-column reconciliation, ambiguity preservation, explicit completion checks, and escalation instead of ad-hoc parser/generated-data edits.
- `regenerate-automatic-parsing` is structurally different: it is a generated-data mutation workflow with dry-run/staging/safeguard/validation/publish phases and explicit protection of `reviewed/**`.

## Design decision

Keep `SKILL.md` as the human-readable workflow authority and add **small machine contracts outside the skill packages** under `agent/harness/capability_manifests/`.

The manifest stores only runtime-relevant identifiers and paths that a controller must know deterministically:

- schema and contract version;
- canonical skill identity and canonical package path;
- alias paths;
- work/context unit;
- ordered stage identifiers;
- scope/safety invariants;
- evidence/dependency identifiers exposed by the source skill;
- repository-relative authoritative dependency paths;
- repository-relative helper/tool paths;
- effect class and writable/read-only path scopes;
- completion verifier identifiers;
- escalation target identifiers;
- evaluator requirements and runtime permissions.

It deliberately does **not** restate the explanatory prose, source precedence rules, linguistic notation, commands, or scholarly argumentation from `SKILL.md` and its references. Evidence identifiers describe capabilities/dependencies available to the workflow; they do not replace the token-level source-selection rules in `SKILL.md`.

## Framework-neutral runtime seam

Add a standard-library-only `agent/harness/skill_capabilities.py` module with:

- immutable `SkillCapabilityManifest` contract;
- `SkillEffect` enum;
- strict JSON manifest parsing/validation;
- `SkillCapabilityRegistry` for canonical-name and alias-path resolution;
- deterministic detection of unmanaged legacy-only `.claude/skills` packages;
- `SkillProvenance` containing manifest, complete canonical skill-package, and declared external repository-resource digests.

No LangGraph, LangChain, Deep Agents, Langfuse, Pydantic, or vendor-specific type belongs in this layer.

## Alias rule

Aliases never acquire an independent capability identity. A `.claude/skills/<name>` symlink that resolves to `.agents/skills/<name>` maps to the canonical manifest and therefore to the same provenance record.

A real `.claude/skills` package with no canonical `.agents` counterpart is **unmanaged legacy**, not silently promoted to canonical status. `parse-ugaritic-passive-participle` must therefore be reported deterministically and rejected by canonical resolution until a later explicit migration decision creates a canonical package/manifest.

## Provenance rule

Do not hard-code Git blob IDs into the capability schema. They become stale after every legitimate skill edit and are unavailable in a plain exported worktree.

Instead compute SHA-256 provenance from repository bytes at load time at three levels:

1. the capability manifest itself;
2. the **complete canonical skill package**, recursively and deterministically, including `SKILL.md`, bundled references, bundled scripts, agent-interface files such as `agents/openai.yaml`, and future package files even when they are not individually listed in the manifest;
3. each declared authoritative/helper resource that lives elsewhere in the repository.

The package digest includes repository-relative package paths and file bytes. Symlink entries include their link target text and are rejected if broken or if they escape the repository. This is intentionally a worktree/content identity rather than a Git-tree identity, so it also works in exported repositories without `.git` metadata.

The resulting provenance object is deterministic and JSON-serializable. A harness trace should store these digests together with the repository commit SHA when Git context is available. External scholarly datasets/services named by evidence identifiers need their own runtime adapter/source-version provenance when actually queried; HARN-008 does not pretend that a symbolic identifier such as `dulat` is itself a source version. That execution-level evidence provenance belongs with the column/eval runtime work in HARN-015/HARN-018.

## First mapped capabilities

### `review-automatic-parsing`

Machine invariants include:

- `complete-column`;
- `every-token-in-order`;
- `worklists-attention-only`;
- `preserve-defensible-ambiguity`;
- `auto-parsing-generated-never-hand-edit`;
- `one-column-per-review-commit`.

Ordered stages:

1. `establish-scope`;
2. `seed-if-needed`;
3. `build-worklist`;
4. `review-each-token`;
5. `verify`;
6. `report`.

Evidence/dependency identifiers retain the sources exposed by the skill, including DULAT, Tropper, EUPT, published translations, legacy review, corpus parallels, and Burns cultic-vocabulary evidence. The machine contract does not invent a separate Burns-optional policy that is absent from the source skill.

Effect class: curated-data write, restricted to `reviewed/**` for the review operation. Parser/linter/tool fixes are escalation targets, not hidden side effects of the review capability.

### `regenerate-automatic-parsing`

Machine invariants include:

- `generated-output-only`;
- `reviewed-read-only`;
- `dry-run-before-write`;
- `stage-before-publish`;
- `step-change-safeguard`;
- `failed-stage-not-resumable`.

Ordered stages:

1. `choose-route`;
2. `snapshot-baseline`;
3. `dry-run`;
4. `regenerate-to-stage`;
5. `validate`;
6. `publish`.

Effect class: generated-data write. The manifest may authorize the generated/versioned output and intentional report paths, but never `reviewed/**`.

## TDD / review record

1. Contract tests were added before `skill_capabilities.py` or real manifests existed. Import happens inside test methods so RED was an ordinary assertion failure, not collection failure.
2. Initial RED was exactly the missing capability module: 6 new failures with the pre-existing suite otherwise green.
3. The first implementation added the framework-neutral registry/provenance layer and two real manifests.
4. The first implementation run exposed two **test-fixture assumptions**, not implementation defects: the synthetic skill lacked required frontmatter, and one semantic sentinel ignored Markdown line wrapping. The tests were corrected without weakening their semantic requirements.
5. First GREEN completed with the full suite.
6. Logically independent adversarial review then rejected that GREEN for two real findings:
   - provenance did not cover undeclared/future files inside a canonical skill package;
   - the review manifest invented a Burns optionality distinction absent from `SKILL.md`.
7. Both findings were converted into tests first. The review-driven RED failed only for those findings.
8. Implementation was then changed to hash the entire canonical package and to align the evidence classification with the source skill.
9. The review-driven implementation returned the full suite to GREEN.
10. Finalization still requires a fresh adversarial review of the exact final head; previous review disposition is not reused as approval.

## Acceptance interpretation

- `SKILL.md` remains primary workflow documentation; manifests contain compact machine identifiers/paths only.
- `review-automatic-parsing` retains complete-column/every-token semantics exactly.
- `regenerate-automatic-parsing` demonstrates that the same contract works for a materially different generated-data mutation workflow.
- Existing skill scripts/references are referenced, not wrapped or relocated.
- Capability/provenance contracts are standard-library and framework neutral.
- Alias and legacy-only behavior are deterministic.
- The complete canonical skill package is content-addressed, so bundled files cannot drift invisibly merely because they were omitted from a manifest list.
- Declared repository-external prompt/reference/helper bytes are individually traceable.
- External scholarly evidence versions are explicitly deferred to execution-time evidence provenance rather than falsely represented by symbolic dependency names.
