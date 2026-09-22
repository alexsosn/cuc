#!/usr/bin/env python3
"""Run stage 1 (lexical linking + POS) with Jev on one reviewed column and score it.

    .venv/bin/python scripts/jev_lexical_stage.py --tablet "KTU 1.6" --column I
    .venv/bin/python scripts/jev_lexical_stage.py --tablet "KTU 1.6" --column I --evidence auto-parsing,dulat
    .venv/bin/python scripts/jev_lexical_stage.py --tablet "KTU 1.6" --column I --dry-run   # test double, no network

Requires TYPESAFE_API_KEY unless --dry-run. Tracing/capture follow the HARN-005/031/032
environment flags (CUC_LANGFUSE_ENABLED, CUC_LANGFUSE_CAPTURE_IO). Writes a metrics-only
report under agent/reports/jev-lexical/ (ignored); the run state (which holds evidence
text) stays under the same ignored directory. Completion gates are stubs until 028b.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness.column_loader import load_column  # noqa: E402
from harness.column_state import ColumnRunState, CompletionGateResult  # noqa: E402
from harness.evidence_adapters import EvidenceCollector, SkillScriptLocator  # noqa: E402
from harness.evidence_policy import ALL_SOURCE_IDS  # noqa: E402
from harness.langfuse_sidecar import LangfuseSidecar, wrap_column_review_adapters  # noqa: E402
from harness.langgraph_column_review import (  # noqa: E402
    ColumnReviewAdapters,
    compile_column_review_graph,
    initial_graph_input,
)
from harness.lexical_stage import (  # noqa: E402
    LexicalReading,
    gold_lexical_readings,
    group_lexical_candidates,
    lemma_key,
    pos_class,
    score_lexical_column,
)
from harness.live_providers import (  # noqa: E402
    ExecutionKind,
    ProviderBudgetPolicy,
    ProviderExecutionPolicy,
    ProviderIOCapture,
    _BudgetLedger,
    _ProviderDecisionRuntime,
)
from harness.model_benchmark import BenchmarkBackendSpec  # noqa: E402
from harness.parsing_evaluation import (  # noqa: E402
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
    measure_column_behavior,
)
from harness.typesafe_jev import JevDecisionPolicy, TypeSafeJevClient, jev_binding  # noqa: E402

SHA0 = "0" * 64


class _DryRunTransport:
    """Deterministic test double: prefers the parser's first lexeme, never abstains."""

    execution_kind = ExecutionKind.TEST_DOUBLE

    def __call__(self, url, headers, body, timeout_seconds):
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "choice":
                keys = [k for k in question["criteria"] if k != "none-of-these"]
                if len(keys) == 1:
                    probs = {keys[0]: 1.0}
                else:
                    probs = {k: (0.9 if k == keys[0] else 0.1 / (len(keys) - 1)) for k in keys}
                probs["none-of-these"] = 0.0
                answers[name] = {"type": "choice", "choice": keys[0], "probabilities": probs, "confidence": 0.9}
            else:
                answers[name] = {"type": "noul", "noul": 0.05}
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}}


def _gold_rows(repo_root: Path, tablet: str, column: str) -> dict[str, list[tuple[str, str, str, str]]]:
    gold: dict[str, list[tuple[str, str, str, str]]] = {}
    current = None
    for raw in (repo_root / "reviewed" / f"{tablet}.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if raw.startswith("#"):
            marker = raw.rstrip("\t")
            current = marker
            continue
        if current is None or not current.startswith(f"# {tablet} {column}:") and not (column == "-" and current.startswith(f"# {tablet} ")):
            continue
        fields = raw.split("\t")
        if len(fields) == 8 and fields[0].isdigit():
            gold.setdefault(fields[0], []).append(tuple(fields[3:7]))
    return gold


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tablet", required=True, help='e.g. "KTU 1.6"')
    parser.add_argument("--column", required=True, help="roman numeral, or - for a columnless tablet")
    parser.add_argument("--tf", default="0.2.8")
    parser.add_argument("--evidence", default=",".join(ALL_SOURCE_IDS), help="comma-separated source ids (auto-parsing is always on)")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--exact-model", default="jev-1.13.0")
    parser.add_argument("--max-input-tokens", type=int, default=6_000_000)
    parser.add_argument("--dry-run", action="store_true", help="test double instead of Jev; no network, no cost")
    parser.add_argument("--verify-singles", action="store_true", help="also ask Jev whether a single candidate fits (review priority only)")
    parser.add_argument("--reports", default=str(HERE.parent / "reports" / "jev-lexical"))
    args = parser.parse_args()

    repo_root = HERE.parents[1]
    enabled = tuple(dict.fromkeys(["auto-parsing", *[s.strip() for s in args.evidence.split(",") if s.strip()]]))
    started = datetime.now(timezone.utc)
    run_id = f"jev-lexical-{args.tablet.replace(' ', '_')}-{args.column}-{started.strftime('%Y%m%dT%H%M%SZ')}"

    loaded = load_column(repo_root, tablet=args.tablet, column=args.column, tf_version=args.tf, repository_revision=_git_revision(repo_root), task_id=run_id)
    locator = SkillScriptLocator(repo_root)
    collector = EvidenceCollector.build(enabled, loaded, locator=locator)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    print(f"{run_id}: {len(loaded.snapshot.tokens)} tokens | evidence policy {collector.policy.sha256[:12]} | absent: {collector.policy.absent_sources or '-'}")

    sidecar = LangfuseSidecar.from_environment()
    capture = ProviderIOCapture() if sidecar.capture_io else None
    policy = JevDecisionPolicy(stage="lexical", verify_single_candidate=args.verify_singles)
    if args.dry_run:
        client = TypeSafeJevClient(api_key_env="TYPESAFE_API_KEY_UNUSED", http_json=_DryRunTransport(), decision_policy=policy, capture=capture)
        client.execution_kind = ExecutionKind.TEST_DOUBLE
        os.environ.setdefault("TYPESAFE_API_KEY_UNUSED", "dry-run")
        kind = ExecutionKind.TEST_DOUBLE
    else:
        client = TypeSafeJevClient(decision_policy=policy, capture=capture)
        kind = ExecutionKind.LIVE_PROVIDER
    spec = BenchmarkBackendSpec("jev-lexical", "typesafe", args.model, args.exact_model, SHA0)
    binding = jev_binding(spec, requested_model=args.model, exact_model_version=args.exact_model, client=client,
                          exact_version_provenance="docs.typesafe.ai/models 2026-09-21", execution_kind=kind)
    budget = ProviderBudgetPolicy(
        max_generation_requests_per_trial=2 * len(loaded.snapshot.tokens) + 10,
        max_generation_requests_per_benchmark=2 * len(loaded.snapshot.tokens) + 10,
        max_input_tokens_per_trial=args.max_input_tokens, max_input_tokens_per_benchmark=args.max_input_tokens,
        max_output_tokens_per_trial=400_000, max_output_tokens_per_benchmark=400_000,
        max_output_tokens_per_request=512, max_retries_per_request=2, timeout_seconds=90.0,
    )
    execution = ProviderExecutionPolicy(budget=budget, allow_paid_live_execution=not args.dry_run)
    workload = ParsingWorkloadRef("CUC", loaded.task.tablet, loaded.task.column, loaded.snapshot.snapshot_id, loaded.snapshot.source_provenance,
                                  loaded.task.repository_revision, loaded.task.capability.canonical_name, loaded.task.capability.contract_version,
                                  loaded.task.capability.provenance_sha256, SHA0, collector.policy.sha256, SHA0)
    identity = ParsingRunIdentity(run_id, workload, "typesafe", args.model, args.exact_model, SHA0)
    runtime = _ProviderDecisionRuntime(binding, identity.model_context_metadata(), execution, _BudgetLedger(budget))
    target = EvaluationTarget(f"gold-{args.tablet}-{args.column}", f"reviewed/{args.tablet}.tsv#{args.column}", "repository", "lexical-stage-scorer", "harness.lexical_stage", SHA0)

    progress = {"n": 0, "t0": time.time()}

    def collect(state, token, skill_context, operation_id):
        progress["n"] += 1
        if progress["n"] % 25 == 0:
            print(f"  token {progress['n']}/{len(loaded.snapshot.tokens)} ({token.surface}) {time.time() - progress['t0']:.0f}s", flush=True)
        return collector.collect_evidence(state, token, skill_context, operation_id)

    def verify(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(gate_id, True, state.decision_revision, (f"stub:{gate_id}",), "stub gate (028b pending)")

    def evaluate(state, skill_context, operation_id):
        return ParsingEvaluationRecord(1, identity, target, state.decision_revision, measure_column_behavior(state), (), (), EfficiencyMetrics(), (f"evaluation:{run_id}",))

    adapters = ColumnReviewAdapters(collector.initialize_skill_context, collect, runtime.adjudicate, runtime.reconcile, verify, evaluate)
    traced = wrap_column_review_adapters(adapters, sidecar, provider_calls=lambda: runtime.calls,
                                         provider_io=(lambda: capture.records) if capture is not None else None)
    graph = compile_column_review_graph(traced)
    output = graph.invoke(initial_graph_input(state), config={"configurable": {"thread_id": run_id}})
    final: ColumnRunState = output["column_state"]
    status = output.get("terminal_status")
    flush = sidecar.flush()
    artifact = runtime.artifact("jev-lexical", str(status))
    print(f"terminal={status} requests={artifact.request_count} local_resolutions={artifact.local_resolutions} "
          f"input_tokens={artifact.input_tokens} output_tokens={artifact.output_tokens} "
          f"retries={artifact.retries} budget_exhausted={artifact.budget_exhausted} tracing={'delivered' if flush.delivered else 'off'}")

    # --- score -------------------------------------------------------------------------
    gold_rows = _gold_rows(repo_root, args.tablet, args.column)
    predicted, gold, offered, parser_first, parser_all, per_token = {}, {}, {}, {}, {}, []
    for tok in loaded.snapshot.tokens:
        g = gold_lexical_readings(gold_rows.get(tok.token_id, []))
        gold[tok.token_id] = g
        decision = final.latest_decision(tok.token_id)
        rows = (decision.to_dict().get("reviewed_rows") or []) if decision else []
        pred = tuple(dict.fromkeys(LexicalReading(lemma_key(r["dulat"]), pos_class(r["pos"])) for r in rows)) or (LexicalReading("?", "?"),)
        predicted[tok.token_id] = pred
        evidence = [e.to_dict() for e in final.evidence if f":{tok.token_id}:evidence:" in e.evidence_id]
        try:
            cands = group_lexical_candidates(evidence, max_candidates=policy.max_candidates)
        except Exception:
            cands = ()
        offered[tok.token_id] = tuple(c.reading for c in cands)
        auto = loaded.automatic_rows[tok.token_id]
        parser_first[tok.token_id] = LexicalReading(lemma_key(auto[0].dulat), pos_class(auto[0].pos))
        parser_all[tok.token_id] = tuple(dict.fromkeys(LexicalReading(lemma_key(r.dulat), pos_class(r.pos)) for r in auto))
        jev = (decision.summary if decision else "")
        per_token.append({"token": tok.token_id, "surface": tok.surface, "line": tok.line_ref,
                          "gold": [r.to_dict() for r in g], "predicted": [r.to_dict() for r in pred],
                          "offered": [r.to_dict() for r in offered[tok.token_id]], "summary": jev})
    score = score_lexical_column(predicted=predicted, gold=gold, offered=offered, parser_first=parser_first, parser_all=parser_all)
    d = score.to_dict()
    print(f"lexical exact {d['exact']}/{d['tokens']} ({d['exact_rate']:.1%}; {d['gold_unresolved']} gold-unresolved excluded) | "
          f"lemma-only {d['lemma_exact_rate']:.1%} | "
          f"abstained correct/wrong {d['abstained_correct']}/{d['abstained_wrong']} | parser first {d['parser_first_rate']:.1%} | "
          f"parser all {d['parser_all_rate']:.1%} | ceiling {d['ceiling_rate']:.1%}")

    reports = Path(args.reports)
    reports.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id, "tablet": args.tablet, "column": args.column, "tf_version": args.tf, "stage": "lexical",
        "model": {"requested": args.model, "exact": args.exact_model, "execution_kind": kind.value},
        "evidence_policy": collector.policy.to_dict(), "evidence_policy_sha256": collector.policy.sha256,
        "terminal_status": status, "provider": {"requests": artifact.request_count, "local_resolutions": artifact.local_resolutions,
                                                 "input_tokens": artifact.input_tokens, "output_tokens": artifact.output_tokens,
                                                 "retries": artifact.retries},
        "adapter_failures": collector.adapter_failures, "score": d, "started": started.isoformat(),
        "seconds": round(time.time() - progress["t0"], 1),
    }
    (reports / f"{run_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (reports / f"{run_id}.tokens.json").write_text(json.dumps(per_token, ensure_ascii=False, indent=1), encoding="utf-8")
    (reports / f"{run_id}.state.json").write_text(final.to_json(), encoding="utf-8")
    print(f"report: {reports / (run_id + '.json')}")
    return 0 if status == "completed" else 1


def _git_revision(repo_root: Path) -> str:
    try:
        import subprocess

        return subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
