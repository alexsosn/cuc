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
- required/optional evidence identifiers;
- repository-relative authoritative dependency paths;
- repository-relative helper/tool paths;
- effect class and writable/read-only path scopes;
- completion verifier identifiers;
- escalation target identifiers;
- evaluator requirements and runtime permissions.

It deliberately does **not** restate the explanatory prose, source precedence rules, linguistic notation, commands, or scholarly argumentation from `SKILL.md` and its references.

## Framework-neutral runtime seam

Add a standard-library-only `agent/harness/skill_capabilities.py` module with:

- immutable `SkillCapabilityManifest` contract;
- `SkillEffect` enum;
- strict JSON manifest parsing/validation;
- `SkillCapabilityRegistry` for canonical-name and alias-path resolution;
- deterministic detection of unmanaged legacy-only `.claude/skills` packages;
- `SkillProvenance` / resource digests computed from the manifest, `SKILL.md`, and declared prompt/reference/tool resources.

No LangGraph, LangChain, Deep Agents, Langfuse, Pydantic, or vendor-specific type belongs in this layer.

## Alias rule

Aliases never acquire an independent capability identity. A `.claude/skills/<name>` symlink that resolves to `.agents/skills/<name>` maps to the canonical manifest and therefore to the same provenance record.

A real `.claude/skills` package with no canonical `.agents` counterpart is **unmanaged legacy**, not silently promoted to canonical status. `parse-ugaritic-passive-participle` must therefore be reported deterministically and rejected by canonical resolution until a later explicit migration decision creates a canonical package/manifest.

## Provenance rule

Do not hard-code Git blob IDs into the capability schema. They become stale after every legitimate skill edit and are unavailable in a plain exported worktree.

Instead compute SHA-256 digests from repository bytes at load time for:

1. the capability manifest itself;
2. canonical `SKILL.md`;
3. every declared authoritative/reference/helper resource.

The resulting provenance object is exact, deterministic, JSON-serializable, and works both inside and outside Git. A harness trace can store these digests together with the repository commit SHA when Git context is available.

## First mapped capabilities

### `review-automatic-parsing`

Machine invariants must include:

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

Effect class: curated-data write, restricted to `reviewed/**` for the review operation. Parser/linter/tool fixes are escalation targets, not hidden side effects of the review capability.

### `regenerate-automatic-parsing`

Machine invariants must include:

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

## TDD plan

1. Add tests before `skill_capabilities.py` or real manifests exist. Import is performed inside test methods so RED is an ordinary assertion failure, not collection failure.
2. Contract tests define strict schema/serialization behavior and reject unknown/vendor-specific fields, scalar/list confusion, unsafe paths, malformed aliases, and unsupported schema versions.
3. Registry tests define canonical resolution, alias canonicalization, deterministic unmanaged-legacy reporting, and provenance hashing.
4. Semantic regression tests compare the `review-automatic-parsing` manifest against sentinel statements in the actual current `SKILL.md`; the manifest cannot claim complete-column/every-token semantics if the source skill no longer says so.
5. Add the two real manifests only after RED is observed.
6. Run targeted tests, then the full `agent/tests` suite.
7. Perform a logically independent adversarial review using only #9 acceptance, the actual skill packages, final diff, and test/eval evidence. Reject invented stages, duplicated scholarly semantics, alias identity duplication, write-scope widening, silent legacy-skill promotion, or provenance that cannot detect source drift.

## Acceptance interpretation

- `SKILL.md` remains primary workflow documentation; manifests contain compact machine identifiers/paths only.
- `review-automatic-parsing` retains complete-column/every-token semantics exactly.
- `regenerate-automatic-parsing` demonstrates that the same contract works for a materially different generated-data mutation workflow.
- Existing skill scripts/references are referenced, not wrapped or relocated.
- Capability/provenance contracts are standard-library and framework neutral.
- Alias and legacy-only behavior are deterministic.
- Exact loaded skill/prompt/reference/tool bytes are traceable through provenance digests.
