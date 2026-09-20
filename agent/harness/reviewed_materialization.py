"""Pure materialization of a completed column review into reviewed TSV text.

This module deliberately owns no filesystem, network, GitHub, seeding, or generated-data
side effects.  It accepts source text plus an already-completed durable column state and
returns replacement text for a caller to inspect or persist elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from .column_state import ColumnRunState, ColumnToken, ReviewedRow, TokenDecision


_HEADER = (
    "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments"
)
_SEED_MARKER = "SEEDED from auto-parse"
_LINE_MARKER_PREFIX = "# KTU "


@dataclass(frozen=True)
class _SourceRow:
    index: int
    fields: tuple[str, ...]
    line_ending: str

    @property
    def token_id(self) -> str:
        return self.fields[0]

    @property
    def surface(self) -> str:
        return self.fields[1]

    @property
    def sign_span(self) -> str:
        return self.fields[2]


@dataclass(frozen=True)
class _TargetBlock:
    token: ColumnToken
    rows: tuple[_SourceRow, ...]
    decision: TokenDecision

    @property
    def first_index(self) -> int:
        return self.rows[0].index

    @property
    def last_index(self) -> int:
        return self.rows[-1].index


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1:]
    return line, ""


def _validate_completed_state(state: ColumnRunState) -> None:
    if not isinstance(state, ColumnRunState):
        raise ValueError("completed_state must be ColumnRunState")
    if not state.initial_pass_complete:
        raise ValueError("materialization requires completed traversal of every snapshot token")
    if state.unresolved_revisits:
        raise ValueError("materialization requires every revisit to be resolved")
    if not state.reconciliation_closed:
        raise ValueError("materialization requires closed reconciliation")

    current_gate_results = {
        result.gate_id: result
        for result in state.gate_results
        if result.decision_revision == state.decision_revision
    }
    for gate_id in state.task.required_completion_gates:
        result = current_gate_results.get(gate_id)
        if result is not None and not result.passed:
            raise ValueError(f"materialization blocked by failed completion gate: {gate_id}")

    if state.completion is None:
        raise ValueError("materialization requires a completed ColumnRunState")
    if state.completion.decision_revision != state.decision_revision:
        raise ValueError("completion revision does not match current decision revision")
    if state.completion.gate_ids != state.task.required_completion_gates:
        raise ValueError("completion gate record does not match task-required gates")


def _latest_structured_decisions(state: ColumnRunState) -> dict[str, TokenDecision]:
    decisions: dict[str, TokenDecision] = {}
    for token in state.snapshot.tokens:
        decision = state.latest_decision(token.token_id)
        if decision is None:
            raise ValueError(f"missing latest decision for snapshot token {token.token_id}")
        if not decision.reviewed_rows:
            raise ValueError(
                f"latest decision for {token.token_id} lacks structured reviewed_rows"
            )
        for row in decision.reviewed_rows:
            if any(_SEED_MARKER in value for value in row.to_dict().values()):
                raise ValueError(
                    f"workflow seed marker cannot be emitted for token {token.token_id}"
                )
        decisions[token.token_id] = decision
    return decisions


def _parse_source_lines(reviewed_tsv_text: str) -> tuple[list[str], tuple[_SourceRow, ...]]:
    if not isinstance(reviewed_tsv_text, str):
        raise ValueError("reviewed_tsv_text must be a string")
    raw_lines = reviewed_tsv_text.splitlines(keepends=True)
    if not raw_lines:
        raise ValueError("reviewed TSV is empty")

    header_body, _ = _split_line_ending(raw_lines[0])
    if header_body != _HEADER:
        raise ValueError("reviewed TSV must use the canonical 8-column header")

    parsed: list[_SourceRow] = []
    for index, line in enumerate(raw_lines[1:], start=1):
        body, ending = _split_line_ending(line)
        fields = tuple(body.split("\t"))
        if len(fields) != 8:
            raise ValueError(
                f"malformed reviewed TSV row at line {index + 1}: expected 8 columns"
            )
        parsed.append(_SourceRow(index, fields, ending))
    return raw_lines, tuple(parsed)


def _source_line_contexts(source_rows: tuple[_SourceRow, ...]) -> dict[int, str | None]:
    """Map each ordinary source row to the nearest preceding KTU line marker."""

    current: str | None = None
    contexts: dict[int, str | None] = {}
    for row in source_rows:
        if row.token_id.startswith(_LINE_MARKER_PREFIX):
            current = row.token_id[2:]
            continue
        contexts[row.index] = current
    return contexts


def _validate_source_column_coverage(
    source_rows: tuple[_SourceRow, ...],
    state: ColumnRunState,
    line_contexts: dict[int, str | None],
) -> None:
    """Require the seeded source target column to equal the complete snapshot token set."""

    prefix = f"{state.task.tablet} {state.task.column}:"
    source_ids: list[str] = []
    seen: set[str] = set()
    for row in source_rows:
        if row.token_id.startswith(_LINE_MARKER_PREFIX):
            continue
        context = line_contexts.get(row.index)
        if context is None or not context.startswith(prefix):
            continue
        if row.token_id not in seen:
            source_ids.append(row.token_id)
            seen.add(row.token_id)

    observed = tuple(source_ids)
    expected = state.snapshot.token_ids
    if observed != expected:
        raise ValueError(
            "source target-column token sequence does not exactly match complete snapshot "
            f"tokens: observed={observed!r}, expected={expected!r}"
        )


def _build_target_blocks(
    source_rows: tuple[_SourceRow, ...],
    state: ColumnRunState,
    decisions: dict[str, TokenDecision],
) -> tuple[_TargetBlock, ...]:
    by_token: dict[str, list[_SourceRow]] = {
        token.token_id: [] for token in state.snapshot.tokens
    }
    for row in source_rows:
        if row.token_id in by_token:
            by_token[row.token_id].append(row)

    line_contexts = _source_line_contexts(source_rows)
    _validate_source_column_coverage(source_rows, state, line_contexts)

    blocks: list[_TargetBlock] = []
    for token in state.snapshot.tokens:
        rows = tuple(by_token[token.token_id])
        if not rows:
            raise ValueError(f"missing source block for target token {token.token_id}")

        indexes = tuple(row.index for row in rows)
        if indexes != tuple(range(indexes[0], indexes[-1] + 1)):
            raise ValueError(f"target token {token.token_id} source block is not contiguous")

        surfaces = {row.surface for row in rows}
        if surfaces != {token.surface}:
            raise ValueError(
                f"source surface for {token.token_id} does not match snapshot surface"
            )
        sign_spans = {row.sign_span for row in rows}
        if len(sign_spans) != 1:
            raise ValueError(
                f"immutable sign span differs across source alternatives for {token.token_id}"
            )

        expected_line_context = f"{state.task.tablet} {token.line_ref}"
        observed_line_contexts = {line_contexts.get(row.index) for row in rows}
        if observed_line_contexts != {expected_line_context}:
            raise ValueError(
                f"source line marker identity for {token.token_id} does not match "
                f"snapshot/task context {expected_line_context!r}"
            )

        blocks.append(_TargetBlock(token, rows, decisions[token.token_id]))

    first_indexes = tuple(block.first_index for block in blocks)
    if first_indexes != tuple(sorted(first_indexes)):
        raise ValueError("source target token order does not match snapshot order")
    return tuple(blocks)


def _render_block(block: _TargetBlock) -> tuple[str, ...]:
    token_id = block.rows[0].token_id
    surface = block.rows[0].surface
    sign_span = block.rows[0].sign_span
    default_ending = block.rows[0].line_ending or "\n"
    final_source_ending = block.rows[-1].line_ending

    rendered: list[str] = []
    reviewed_rows = block.decision.reviewed_rows
    for position, row in enumerate(reviewed_rows):
        ending = default_ending
        if position == len(reviewed_rows) - 1 and final_source_ending == "":
            ending = ""
        fields = (
            token_id,
            surface,
            sign_span,
            row.morphological_parsing,
            row.dulat,
            row.pos,
            row.gloss,
            row.comments,
        )
        rendered.append("\t".join(fields) + ending)
    return tuple(rendered)


def materialize_completed_column(
    reviewed_tsv_text: str,
    completed_state: ColumnRunState,
) -> str:
    """Return a deterministic reviewed-TSV draft for one completed column.

    Existing immutable token identity fields come from the already-seeded source TSV;
    all scholarly mutable fields come exclusively from the latest structured decisions.
    Rows outside the snapshot are returned unchanged.
    """

    _validate_completed_state(completed_state)
    decisions = _latest_structured_decisions(completed_state)
    raw_lines, source_rows = _parse_source_lines(reviewed_tsv_text)
    blocks = _build_target_blocks(source_rows, completed_state, decisions)

    replacement_at = {block.first_index: _render_block(block) for block in blocks}
    skipped_indexes = {
        row.index
        for block in blocks
        for row in block.rows
        if row.index != block.first_index
    }

    output: list[str] = []
    for index, line in enumerate(raw_lines):
        replacement = replacement_at.get(index)
        if replacement is not None:
            output.extend(replacement)
            continue
        if index in skipped_indexes:
            continue
        output.append(line)
    return "".join(output)
