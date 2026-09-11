import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
UPSTREAM = "DT-UCPH/cuc"
DIRECT_GITHUB_TRANSPORT_MARKERS = (
    "api.github.com",
    "gh pr create",
    "gh issue create",
    "gh pr comment",
    "gh issue comment",
    "requests.post(",
    "requests.patch(",
    "httpx.post(",
    "httpx.patch(",
    "urllib.request",
    "subprocess.run(",
    "subprocess.Popen(",
)


def _workflow_texts():
    if not WORKFLOW_DIR.exists():
        return []
    return [path.read_text(encoding="utf-8") for path in WORKFLOW_DIR.glob("*.y*ml")]


def test_workflows_do_not_use_dangerous_external_triggers():
    for text in _workflow_texts():
        assert "pull_request_target:" not in text
        assert not re.search(r"(?m)^\s*schedule\s*:", text)


def test_workflow_push_scope_is_not_broadened_beyond_main():
    for text in _workflow_texts():
        if not re.search(r"(?m)^\s*push\s*:", text):
            continue
        branch_lists = re.findall(r"branches\s*:\s*\[([^\]]*)\]", text)
        assert branch_lists, "push workflows must declare an explicit branch allowlist"
        for branch_list in branch_lists:
            branches = {
                item.strip().strip("'\"")
                for item in branch_list.split(",")
                if item.strip()
            }
            assert branches <= {"main"}, f"unsafe push branch scope: {sorted(branches)}"


def test_workflows_do_not_grant_issue_or_pull_request_write_permissions():
    for text in _workflow_texts():
        lowered = text.lower()
        assert not re.search(r"(?m)^\s*issues\s*:\s*write\s*$", lowered)
        assert not re.search(r"(?m)^\s*pull-requests\s*:\s*write\s*$", lowered)


def test_automation_does_not_target_upstream_writes():
    roots = [
        REPO_ROOT / ".github",
        REPO_ROOT / "scripts",
        REPO_ROOT / "agent" / "scripts",
        REPO_ROOT / "agent" / "harness",
    ]
    upstream_write_markers = (
        "gh pr create",
        "gh issue create",
        "gh pr comment",
        "gh issue comment",
        "repos/DT-UCPH/cuc/pulls",
        "repos/DT-UCPH/cuc/issues",
        "api.github.com/repos/DT-UCPH/cuc/actions/workflows",
    )

    offenders = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".sh", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if UPSTREAM not in text:
                continue
            if any(marker in text for marker in upstream_write_markers):
                offenders.append(str(path.relative_to(REPO_ROOT)))
            elif path.is_relative_to(REPO_ROOT / "agent" / "harness") and any(
                marker in text for marker in DIRECT_GITHUB_TRANSPORT_MARKERS
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"automation may write to upstream {UPSTREAM}: {offenders}"
