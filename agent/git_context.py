import re
import subprocess
from pathlib import Path

from config import (
    CONTEXT_MAX_BRANCHES,
    CONTEXT_MAX_COMMITS_PER_BRANCH,
    CONTEXT_MAX_FILES_PER_BRANCH,
    REPO_PATH,
)


# Directories that bridge.py writes entity JSON files into.
# Extend this list if you use a different --output-dir.
ENTITY_DIRS = ["entities"]


def _run_git(args: list[str], repo_path: str) -> tuple[str, int]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or result.stderr).strip()
    return output, result.returncode


from config import (
    CONTEXT_MAX_BRANCHES,
    CONTEXT_MAX_COMMITS_PER_BRANCH,
    CONTEXT_MAX_FILES_PER_BRANCH,
    REPO_PATH,
    DEFAULT_BRANCH,
)


def _detect_default_branch(repo_path: str) -> str:
    if DEFAULT_BRANCH:
        _, code = _run_git(["rev-parse", "--verify", DEFAULT_BRANCH], repo_path)
        if code == 0:
            return DEFAULT_BRANCH

    output, code = _run_git(
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        repo_path,
    )
    if code == 0 and output:
        return output.rsplit("/", 1)[-1]

    for branch in ("main", "master", "developer", "develop"):
        _, code = _run_git(["rev-parse", "--verify", branch], repo_path)
        if code == 0:
            return branch

    output, code = _run_git(["branch", "--show-current"], repo_path)
    if code == 0 and output:
        return output

    return "main"


def _list_local_branches(repo_path: str) -> list[dict[str, str]]:
    output, code = _run_git(
        [
            "for-each-ref",
            "refs/heads/",
            "--sort=-committerdate",
            "--format=%(refname:short)|%(objectname:short)|%(subject)",
        ],
        repo_path,
    )
    if code != 0 or not output:
        return []

    branches = []
    for line in output.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            branches.append(
                {"name": parts[0], "tip": parts[1], "subject": parts[2]}
            )
    return branches[:CONTEXT_MAX_BRANCHES]


def _parse_log_with_files(raw: str) -> list[dict]:
    commits: list[dict] = []
    current: dict | None = None

    for line in raw.splitlines():
        if line.startswith("COMMIT|"):
            if current:
                commits.append(current)
            _, short_hash, subject = line.split("|", 2)
            current = {
                "hash": short_hash,
                "subject": subject,
                "files": [],
            }
            continue

        if not current or not line.strip():
            continue

        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, path = parts
            current["files"].append(f"{status} {path}")

    if current:
        commits.append(current)

    return commits


def _commits_for_branch(repo_path: str, branch: str) -> list[dict]:
    raw, code = _run_git(
        [
            "log",
            branch,
            f"-{CONTEXT_MAX_COMMITS_PER_BRANCH}",
            "--no-decorate",
            "--format=COMMIT|%h|%s",
            "--name-status",
        ],
        repo_path,
    )
    if code != 0:
        return []
    return _parse_log_with_files(raw)


def _files_at_ref(repo_path: str, ref: str) -> list[str]:
    output, code = _run_git(
        ["ls-tree", "-r", "--name-only", ref],
        repo_path,
    )
    if code != 0 or not output:
        return []
    files = output.splitlines()
    if len(files) > CONTEXT_MAX_FILES_PER_BRANCH:
        extra = len(files) - CONTEXT_MAX_FILES_PER_BRANCH
        files = files[:CONTEXT_MAX_FILES_PER_BRANCH]
        files.append(f"... and {extra} more files")
    return files


def _merge_base(repo_path: str, branch_a: str, branch_b: str) -> str:
    output, code = _run_git(
        ["merge-base", branch_a, branch_b],
        repo_path,
    )
    return output if code == 0 else ""


def _commits_unique_to_branch(
    repo_path: str, branch: str, base_branch: str
) -> list[str]:
    output, code = _run_git(
        [
            "log",
            f"{base_branch}..{branch}",
            "--oneline",
            f"-{CONTEXT_MAX_COMMITS_PER_BRANCH}",
            "--no-decorate",
        ],
        repo_path,
    )
    if code != 0 or not output:
        return []
    return output.splitlines()


def _extract_query_branches(query: str, known_branches: list[str]) -> list[str]:
    if not query:
        return []

    mentioned = []
    query_lower = query.lower()
    for branch in known_branches:
        branch_lower = branch.lower()
        # Use plain substring match instead of word-boundary regex so that
        # branch names containing slashes and hyphens (e.g. feature/customer-x)
        # are still found when the user mentions them in their query.
        if branch_lower in query_lower:
            mentioned.append(branch)
    return mentioned


def _format_commit_index(branch: str, commits: list[dict]) -> str:
    if not commits:
        return f"=== {branch} ===\n(no commits)"

    lines = [f"=== {branch} (commit #1 = newest) ==="]
    for idx, commit in enumerate(commits, start=1):
        lines.append(f"#{idx} {commit['hash']} | {commit['subject']}")
        if commit["files"]:
            for file_entry in commit["files"]:
                lines.append(f"    {file_entry}")
        else:
            lines.append("    (no file changes)")
    return "\n".join(lines)


def _format_file_index(
    branch_commits: dict[str, list[dict]],
) -> str:
    file_map: dict[str, list[str]] = {}

    for branch, commits in branch_commits.items():
        for idx, commit in enumerate(commits, start=1):
            for file_entry in commit["files"]:
                path = file_entry.split(" ", 1)[-1]
                file_map.setdefault(path, []).append(
                    f"#{idx} on {branch} ({commit['hash']})"
                )

    if not file_map:
        return "(no file change data)"

    lines = []
    for path in sorted(file_map):
        refs = ", ".join(file_map[path][:5])
        if len(file_map[path]) > 5:
            refs += f", +{len(file_map[path]) - 5} more"
        lines.append(f"{path}: {refs}")
    return "\n".join(lines)


def _format_branch_heads(
    repo_path: str, branches: list[dict[str, str]]
) -> str:
    lines = []
    for branch in branches:
        files = _files_at_ref(repo_path, branch["name"])
        preview = ", ".join(files[:8])
        if len(files) > 8:
            preview += f", ... ({len(files)} files total)"
        lines.append(
            f"{branch['name']} @ {branch['tip']}: {preview or '(empty tree)'}"
        )
    return "\n".join(lines)


def _entity_files_on_branch(repo_path: str, branch: str) -> list[str]:
    """Return all entity JSON file paths that exist on `branch` under ENTITY_DIRS."""
    found = []
    for entity_dir in ENTITY_DIRS:
        output, code = _run_git(
            ["ls-tree", "-r", "--name-only", branch, entity_dir],
            repo_path,
        )
        if code == 0 and output:
            for line in output.splitlines():
                line = line.strip()
                if line.endswith(".json"):
                    found.append(line)
    return found


def _read_file_at_ref(repo_path: str, ref: str, filepath: str) -> str | None:
    """Read the contents of `filepath` at git ref `ref` using git show."""
    output, code = _run_git(["show", f"{ref}:{filepath}"], repo_path)
    if code != 0:
        return None
    return output


def _format_entity_contents(repo_path: str, branches_to_check: list[str]) -> str:
    """
    For each branch in `branches_to_check`, read every entity JSON file and
    return a formatted block that the LLM can use to answer questions like
    'show valuetypes in this branch'.
    """
    import json as _json

    sections = []
    seen: dict[str, set] = {}  # branch -> set of paths already emitted

    for branch in branches_to_check:
        paths = _entity_files_on_branch(repo_path, branch)
        if not paths:
            continue

        branch_lines = [f"=== Entity files on branch: {branch} ==="]
        seen.setdefault(branch, set())

        for path in paths:
            if path in seen[branch]:
                continue
            seen[branch].add(path)

            raw = _read_file_at_ref(repo_path, branch, path)
            if raw is None:
                branch_lines.append(f"  {path}: (could not read)")
                continue

            # Pretty-print JSON; fall back to raw text if it isn't valid JSON.
            try:
                parsed = _json.loads(raw)
                content = _json.dumps(parsed, indent=2)
            except _json.JSONDecodeError:
                content = raw

            branch_lines.append(f"  FILE: {path}")
            # Indent every line of the content for readability in the prompt
            for content_line in content.splitlines():
                branch_lines.append(f"    {content_line}")

        if len(branch_lines) > 1:  # at least one file was found
            sections.append("\n".join(branch_lines))

    return "\n\n".join(sections) if sections else "(no entity files found in tracked directories)"


def gather_repo_context(
    repo_path: str | None = None,
    user_query: str = "",
) -> dict[str, str]:
    repo_path = repo_path or REPO_PATH
    repo = Path(repo_path)

    if not (repo / ".git").exists():
        return {
            "repo_path": str(repo),
            "valid": "false",
            "summary": f"ERROR: {repo_path} is not a git repository.",
            "default_branch": "",
            "repo_rules": "",
        }

    top_level, _ = _run_git(["rev-parse", "--show-toplevel"], repo_path)
    current_branch, _ = _run_git(["branch", "--show-current"], repo_path)
    all_branches_raw_full, _ = _run_git(["branch", "-a", "--no-color"], repo_path)
    all_branches_lines = all_branches_raw_full.splitlines() if all_branches_raw_full else []
    all_branches_limited = all_branches_lines[:CONTEXT_MAX_BRANCHES * 2]
    all_branches_raw = "\n".join(all_branches_limited)
    if len(all_branches_lines) > len(all_branches_limited):
        all_branches_raw += f"\n... and {len(all_branches_lines) - len(all_branches_limited)} more branches"
    remotes, _ = _run_git(["remote", "-v"], repo_path)
    status, _ = _run_git(["status", "-sb"], repo_path)
    graph, _ = _run_git(
        ["log", "--all", "--oneline", "--graph", "-20", "--no-decorate"],
        repo_path,
    )

    default_branch = _detect_default_branch(repo_path)
    local_branches = _list_local_branches(repo_path)
    branch_names = [b["name"] for b in local_branches]

    priority = {default_branch, current_branch}
    priority.update(_extract_query_branches(user_query, branch_names))

    branches_to_index = []
    seen = set()
    for name in list(priority) + branch_names:
        if name and name not in seen:
            seen.add(name)
            branches_to_index.append(name)

    branch_commits: dict[str, list[dict]] = {}
    commit_index_sections = []
    for branch in branches_to_index[:CONTEXT_MAX_BRANCHES]:
        commits = _commits_for_branch(repo_path, branch)
        branch_commits[branch] = commits
        commit_index_sections.append(_format_commit_index(branch, commits))

    branch_lines = [
        f"- {b['name']}: tip {b['tip']} | {b['subject']}"
        for b in local_branches
    ]

    divergence_sections = []
    if default_branch in branch_commits:
        for branch in branches_to_index:
            if branch == default_branch:
                continue
            unique = _commits_unique_to_branch(
                repo_path, branch, default_branch
            )
            if unique:
                divergence_sections.append(
                    f"Commits on {branch} not in {default_branch}:\n"
                    + "\n".join(unique)
                )
            base = _merge_base(repo_path, default_branch, branch)
            if base:
                divergence_sections.append(
                    f"merge-base({default_branch}, {branch}) = {base}"
                )

    # Read entity JSON file contents.
    # Scan ALL local branches so a query like "show valuetypes in feature/customer-valuetype2"
    # works even when the branch name contains slashes/hyphens that break word-boundary
    # matching in _extract_query_branches.
    # Current branch and query-mentioned branches are listed first for relevance.
    entity_branches = list(dict.fromkeys(
        [b for b in [current_branch] + list(priority) + branch_names if b]
    ))
    entity_contents = _format_entity_contents(repo_path, entity_branches)

    repo_rules = f"""
- Default branch: '{default_branch}'. Use when no source branch is specified.
- Commit numbering: #1 = newest on that branch (top of git log --oneline).
- Each commit in context lists files changed (A=added, M=modified, D=deleted).
- To build a branch from SELECTED commits across branches:
  1. Create the new branch from the chosen base (usually default branch tip).
  2. Cherry-pick only the requested commits in order (oldest first).
  3. Use exact hashes from the commit index — never invent hashes.
- If user says "commit 2 from feature-x", use #2 hash from feature-x in context.
- Repository root: {top_level or repo_path}
- Current branch: {current_branch or '(detached HEAD)'}
""".strip()

    summary = f"""Repository: {top_level or repo_path}
Default branch: {default_branch}
Current branch: {current_branch or '(detached HEAD)'}

BRANCHES (local):
{chr(10).join(branch_lines) or '(none)'}

ALL BRANCHES (including remotes):
{all_branches_raw or '(none)'}

REMOTES:
{remotes or '(none)'}

WORKING TREE:
{status or '(clean)'}

RECENT GRAPH (all branches):
{graph or '(empty)'}

COMMIT INDEX (files per commit):
{chr(10).join(commit_index_sections)}

FILE -> COMMIT MAP (recent changes):
{_format_file_index(branch_commits)}

FILES AT BRANCH HEADS:
{_format_branch_heads(repo_path, local_branches)}

BRANCH DIVERGENCE:
{chr(10).join(divergence_sections) if divergence_sections else '(branches align or single branch)'}

ENTITY FILE CONTENTS (JSON files committed under {", ".join(ENTITY_DIRS)}):
{entity_contents}
"""

    return {
        "repo_path": top_level or str(repo),
        "valid": "true",
        "summary": summary.strip(),
        "default_branch": default_branch,
        "repo_rules": repo_rules,
    }