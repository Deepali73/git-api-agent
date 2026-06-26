"""Bridge script connecting the backend payload generator and the GitOps agent.

FLOWS:
  backend   — call backend API only
  git       — run git agent only (raw --git-query)
  combined  — backend → create branch → write entity JSON → commit
  collect   — cherry-pick selected commits from one or more branches
               into a new target branch (no backend call needed)

EXAMPLES:

  # Create entity + commit to branch:
  python bridge.py --flow combined --entity ValueType \\
      --intent "create a Customer value type in package ecom" \\
      --branch feature/customer

  # Take ALL commits from one branch into a new branch:
  python bridge.py --flow collect \\
      --from-branches feature/customer \\
      --target-branch release/v1

  # Take ALL commits from MULTIPLE branches into one target:
  python bridge.py --flow collect \\
      --from-branches feature/customer feature/order feature/product \\
      --target-branch release/v1

  # Take only SPECIFIC commits (by number, #1=newest) from a branch:
  python bridge.py --flow collect \\
      --from-branches feature/customer \\
      --commits "feature/customer:1,3" \\
      --target-branch release/v1

  # Mix: all commits from branch-A, specific commits from branch-B:
  python bridge.py --flow collect \\
      --from-branches feature/customer feature/order \\
      --commits "feature/order:1,2" \\
      --target-branch release/v1

  # After collecting, query what ended up in the target:
  python bridge.py --flow git \\
      --git-query "show valuetypes in branch release/v1"
"""

import argparse
import json
import os
import re
import sys
import subprocess

import importlib.util as _ilu

_AGENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent")
sys.path.insert(0, _AGENT_DIR)

from python.agent import run_intent
from python.backend_client import BackendApiError
from agent.main import run_agent_query
import python.config as python_config

_agent_config_spec = _ilu.spec_from_file_location(
    "agent_config", os.path.join(_AGENT_DIR, "config.py")
)
agent_config = _ilu.module_from_spec(_agent_config_spec)
_agent_config_spec.loader.exec_module(agent_config)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backend payload generation + GitOps agent bridge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--flow",
        choices=["backend", "git", "combined", "collect"],
        default="combined",
        help="Which flow to run (default: combined).",
    )

    # Backend args
    parser.add_argument("--entity", help="Backend entity name e.g. ValueType.")
    parser.add_argument("--op", choices=["create", "update"], default="create")
    parser.add_argument("--id", dest="entity_id", help="Entity id for update.")
    parser.add_argument("--intent", help="Natural language intent.")
    parser.add_argument("--ticket", help="Ticket-style natural language intent.")
    parser.add_argument("--no-grounding", action="store_true")
    parser.add_argument(
        "--on-conflict", choices=["abort", "update", "skip"], default="update"
    )

    # Git / combined args
    parser.add_argument("--branch", help="Target branch for combined flow.")
    parser.add_argument("--base-branch", default=None,
                        help="Base branch to branch off from.")
    parser.add_argument("--output-dir", default="entities",
                        help="Repo sub-dir for entity JSON files (default: entities/).")
    parser.add_argument("--git-query", help="Raw query for git-only flow.")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--print-full-state", action="store_true")

    # Collect flow args
    parser.add_argument(
        "--from-branches",
        nargs="+",
        metavar="BRANCH",
        help=(
            "collect flow: one or more source branches to pull commits from. "
            "e.g. --from-branches feature/a feature/b feature/c"
        ),
    )
    parser.add_argument(
        "--target-branch",
        help="collect flow: name of the new branch to create and cherry-pick into.",
    )
    parser.add_argument(
        "--commits",
        nargs="+",
        metavar="BRANCH:N,M,...",
        help=(
            "collect flow: restrict which commits to take from a branch. "
            "Format: BRANCH:1,2,3  where numbers are commit positions (#1=newest). "
            "Branches not listed here have ALL their commits taken. "
            "e.g. --commits feature/order:1,2  feature/product:1"
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_value(value):
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if hasattr(value, "content") and isinstance(value.content, str):
        return value.content
    return value


def _serialize_state(state: dict, full: bool = False) -> dict:
    state = _serialize_value(state)
    if not full:
        return {k: v for k, v in state.items() if k != "repo_context"}
    return state


def _slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40]


def _derive_branch_name(entity: str, intent: str) -> str:
    return f"feature/{_slug(entity)}-{_slug(intent)}"


def _run_git(args: list[str], cwd: str) -> tuple[str, int]:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )
    return (result.stdout + result.stderr).strip(), result.returncode


def _default_branch(repo_path: str) -> str:
    _gc_spec = _ilu.spec_from_file_location(
        "git_context", os.path.join(_AGENT_DIR, "git_context.py")
    )
    _gc_mod = _ilu.module_from_spec(_gc_spec)
    _gc_spec.loader.exec_module(_gc_mod)
    ctx = _gc_mod.gather_repo_context(repo_path)
    return ctx.get("default_branch") or "master"


def _parse_commit_selectors(commits_args: list[str] | None) -> dict[str, list[int]]:
    """
    Parse --commits arguments into a dict: branch -> [commit numbers].
    e.g. ["feature/order:1,2", "feature/product:1"]
      -> {"feature/order": [1, 2], "feature/product": [1]}
    """
    if not commits_args:
        return {}
    result = {}
    for item in commits_args:
        if ":" not in item:
            print(f"WARNING: --commits '{item}' has no colon, skipping.")
            continue
        branch, nums = item.rsplit(":", 1)
        try:
            result[branch.strip()] = [int(n.strip()) for n in nums.split(",") if n.strip()]
        except ValueError:
            print(f"WARNING: --commits '{item}' has invalid numbers, skipping.")
    return result


# ---------------------------------------------------------------------------
# Combined flow helpers
# ---------------------------------------------------------------------------

def _commit_entity_to_branch(
    repo_path: str,
    branch_name: str,
    base_branch: str | None,
    entity_name: str,
    backend_result: dict,
    output_dir: str,
    max_steps: int,
) -> dict:
    repo_path = repo_path or agent_config.REPO_PATH
    base = base_branch or _default_branch(repo_path)

    print(f"\n[bridge] Creating branch '{branch_name}' from '{base}'...")
    branch_result = run_agent_query(
        f"create branch {branch_name} from {base}",
        max_steps=max_steps,
    )
    if not branch_result.get("done"):
        print("[bridge] WARNING: branch creation did not finish cleanly.")

    print(f"[bridge] Checking out '{branch_name}'...")
    out, code = _run_git(["checkout", branch_name], repo_path)
    if code != 0:
        raise RuntimeError(f"Could not check out '{branch_name}': {out}")

    entities_dir = os.path.join(repo_path, output_dir)
    os.makedirs(entities_dir, exist_ok=True)

    entity_id = backend_result.get("id") or "unknown"
    filename = f"{entity_name}_{entity_id}.json"
    filepath = os.path.join(entities_dir, filename)

    with open(filepath, "w", encoding="utf-8") as fh:
        json.dump(backend_result, fh, indent=2)
    print(f"[bridge] Wrote entity file: {filepath}")

    out, code = _run_git(["add", filepath], repo_path)
    if code != 0:
        raise RuntimeError(f"git add failed: {out}")

    # Commit directly via git (not via the agent) to avoid the agent
    # misinterpreting the commit message as a branch/ref name and creating
    # stray folders like "agent~commit initial-commit".
    commit_message = f"feat({entity_name}): add {entity_id} from backend"
    print(f"[bridge] Committing: {commit_message}")
    out, code = _run_git(["commit", "-m", commit_message], repo_path)
    if code != 0:
        raise RuntimeError(f"git commit failed: {out}")
    print(f"[bridge] Committed: {out.splitlines()[0]}")
    return {"done": True}


# ---------------------------------------------------------------------------
# Backend flow helper
# ---------------------------------------------------------------------------

def _run_backend_flow(args: argparse.Namespace) -> dict | None:
    intent_text = args.ticket or args.intent
    if not args.entity or not intent_text:
        print("Error: --entity and --intent/--ticket are required.")
        return None

    print("Running backend payload flow...")
    try:
        return run_intent(
            entity=args.entity,
            intent=intent_text,
            op=args.op,
            entity_id=args.entity_id,
            no_grounding=args.no_grounding,
        )
    except BackendApiError as error:
        print(f"Backend error: {error} (status={error.status})")
        if error.status == 409:
            entity_id = args.entity_id
            if not entity_id and isinstance(error.data, dict):
                entity_id = error.data.get("id") or error.data.get("entityId")

            if args.on_conflict == "skip":
                print("Conflict — skipping.")
                return {}

            if args.on_conflict == "update":
                if not entity_id:
                    try:
                        entity_id = input(
                            "Conflict. Enter existing entity id to update, or blank to abort: "
                        ).strip()
                    except Exception:
                        entity_id = ""
                if entity_id:
                    try:
                        return run_intent(
                            entity=args.entity,
                            intent=intent_text,
                            op="update",
                            entity_id=entity_id,
                            no_grounding=args.no_grounding,
                        )
                    except Exception as inner:
                        print(f"Update failed: {inner}")
                        return None
                print("No id — aborting.")
                return None

        print(f"Backend flow failed: {error}")
        return None
    except Exception as error:
        print(f"Backend flow failed: {error}")
        return None


# ---------------------------------------------------------------------------
# Collect flow
# ---------------------------------------------------------------------------

def _run_collect_flow(args: argparse.Namespace) -> int:
    """
    Creates a target branch and cherry-picks commits from one or more
    source branches into it.

    Branch creation and checkout are done directly via git (not the agent)
    to avoid the agent looping on 'branch already exists' errors.
    Cherry-picks are also done directly for reliability.
    """
    if not args.from_branches:
        print("Error: --from-branches is required for collect flow.")
        return 1
    if not args.target_branch:
        print("Error: --target-branch is required for collect flow.")
        return 1

    repo_path = agent_config.REPO_PATH
    base = args.base_branch or _default_branch(repo_path)
    target = args.target_branch
    commit_selectors = _parse_commit_selectors(args.commits)

    print(f"\n[collect] Target branch  : {target}")
    print(f"[collect] Base branch    : {base}")
    print(f"[collect] Source branches: {args.from_branches}")
    if commit_selectors:
        print(f"[collect] Commit selectors: {commit_selectors}")
    else:
        print("[collect] Taking ALL commits from each source branch.")

    # ---- Step 1: abort any in-progress merge/cherry-pick state --------------
    _run_git(["cherry-pick", "--abort"], repo_path)
    _run_git(["merge", "--abort"], repo_path)

    # ---- Step 2: create or reuse target branch directly ---------------------
    _, code = _run_git(["rev-parse", "--verify", target], repo_path)
    if code == 0:
        print(f"\n[collect] Branch '{target}' already exists — using it.")
        out, code = _run_git(["checkout", target], repo_path)
        if code != 0:
            print(f"[collect] ERROR: could not checkout '{target}': {out}")
            return 1
    else:
        print(f"\n[collect] Creating '{target}' from '{base}'...")
        out, code = _run_git(["checkout", "-b", target, base], repo_path)
        if code != 0:
            print(f"[collect] ERROR: could not create '{target}': {out}")
            return 1
    print(f"[collect] On branch '{target}'. ✓")

    # ---- Step 3: for each source branch, cherry-pick directly ---------------
    for branch in args.from_branches:
        selected = commit_selectors.get(branch)  # None = take all

        # Get commit hashes for this branch (oldest first for cherry-pick order)
        log_out, log_code = _run_git(
            ["log", branch, "--oneline", "--no-decorate", "--format=%H"],
            repo_path,
        )
        if log_code != 0 or not log_out:
            print(f"\n[collect] WARNING: could not read commits from '{branch}', skipping.")
            continue

        # hashes[0] = newest (#1), hashes[-1] = oldest
        all_hashes = log_out.splitlines()

        if selected:
            # selected numbers are 1-based, newest first
            # cherry-pick oldest first so history is correct
            hashes_to_pick = []
            for num in selected:
                idx = num - 1
                if idx < 0 or idx >= len(all_hashes):
                    print(f"  WARNING: commit #{num} does not exist on '{branch}' (only {len(all_hashes)} commits), skipping.")
                    continue
                hashes_to_pick.append(all_hashes[idx])
            # reverse so we cherry-pick oldest first
            hashes_to_pick = list(reversed(hashes_to_pick))
            nums_str = ", ".join(f"#{n}" for n in selected)
            print(f"\n[collect] Cherry-picking commits {nums_str} from '{branch}'...")
        else:
            # All commits — oldest first
            hashes_to_pick = list(reversed(all_hashes))
            print(f"\n[collect] Cherry-picking ALL {len(hashes_to_pick)} commits from '{branch}'...")

        for h in hashes_to_pick:
            short = h[:7]
            print(f"  → cherry-pick {short}...")
            out, code = _run_git(["cherry-pick", h], repo_path)
            if code != 0:
                # Check if it's just "nothing to commit" (already applied)
                if "nothing to commit" in out or "already applied" in out.lower() or "empty commit" in out.lower():
                    print(f"    (already applied, skipping)")
                    _run_git(["cherry-pick", "--skip"], repo_path)
                    continue
                print(f"  ERROR cherry-picking {short}: {out}")
                print(f"  Aborting cherry-pick and continuing to next branch.")
                _run_git(["cherry-pick", "--abort"], repo_path)
                break

    # ---- Step 4: verify final state -----------------------------------------
    print(f"\n[collect] Done. Final commits on '{target}':")
    log_out, _ = _run_git(["log", target, "--oneline", "--no-decorate", "-10"], repo_path)
    print(log_out or "(no commits)")

    print(f"\n[collect] Entity files on '{target}':")
    ls_out, _ = _run_git(["ls-tree", "-r", "--name-only", target, "entities"], repo_path)
    print(ls_out or "(no entity files)")

    print(f"\n✓ collect flow complete. Branch '{target}' is ready.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) == 1:
        parse_args()
        return 0

    args = parse_args()

    # BACKEND only
    if args.flow == "backend":
        result = _run_backend_flow(args)
        if result is None:
            return 1
        print("\nBackend result:")
        print(json.dumps(result, indent=2))
        return 0

    # GIT only
    if args.flow == "git":
        if not args.git_query:
            print("Error: --git-query is required for git flow.")
            return 1
        print("Running git agent flow...")
        try:
            git_result = run_agent_query(args.git_query, max_steps=args.max_steps)
        except Exception as error:
            print(f"Git agent flow failed: {error}")
            return 1
        print("\nGit agent result:")
        print(json.dumps(_serialize_state(git_result, full=args.print_full_state), indent=2))
        return 0

    # COLLECT — multi-branch cherry-pick
    if args.flow == "collect":
        return _run_collect_flow(args)

    # COMBINED — backend + branch + commit
    backend_result = _run_backend_flow(args)
    if backend_result is None:
        return 1

    print("\nBackend result:")
    print(json.dumps(backend_result, indent=2))

    intent_text = args.ticket or args.intent or ""
    branch_name = args.branch or _derive_branch_name(args.entity or "entity", intent_text)
    print(f"\n[bridge] Target branch: {branch_name}")

    try:
        commit_result = _commit_entity_to_branch(
            repo_path=agent_config.REPO_PATH,
            branch_name=branch_name,
            base_branch=args.base_branch,
            entity_name=args.entity,
            backend_result=backend_result,
            output_dir=args.output_dir,
            max_steps=args.max_steps,
        )
    except Exception as error:
        print(f"\n[bridge] Git commit step failed: {error}")
        return 1

    print("\nGit agent commit result:")
    print(json.dumps(_serialize_state(commit_result, full=args.print_full_state), indent=2))

    if commit_result.get("done"):
        print(f"\n✓ Entity '{args.entity}' created in backend and committed to '{branch_name}'.")
    else:
        print(f"\nWARNING: git agent did not finish cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())