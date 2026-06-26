"""
Run this from your repo root to diagnose why entity files are not
appearing in the git agent context.

    python debug_context.py --branch feature/customer-valuetype3

It checks every layer independently so you can see exactly where it breaks.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

# Always use the folder where THIS script lives as the repo root,
# regardless of what git finds by walking up the tree.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR  = os.path.join(SCRIPT_DIR, "agent")
ENTITY_DIRS = ["entities"]


def run_git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip(), r.returncode


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True, help="Branch to inspect")
    args = parser.parse_args()
    branch = args.branch

    # ------------------------------------------------------------------ #
    # 1. Confirm script location and repo root
    # ------------------------------------------------------------------ #
    section("1. Paths")
    print(f"Script dir : {SCRIPT_DIR}")
    print(f"Agent dir  : {AGENT_DIR}")

    # Check if script dir is itself a git repo
    git_dir = os.path.join(SCRIPT_DIR, ".git")
    if not os.path.exists(git_dir):
        print(f"\nERROR: No .git folder found in {SCRIPT_DIR}")
        print("git-api-agent is NOT its own git repository.")
        print("\nFix — run these commands:")
        print(f"  cd {SCRIPT_DIR}")
        print("  git init")
        print("  git add .")
        print('  git commit -m "initial commit"')
        print("\nThen re-run bridge.py to create your feature branch.")
        sys.exit(1)

    repo_root = SCRIPT_DIR
    print(f"Repo root  : {repo_root}  (.git found ✓)")

    # Also show what git itself thinks the root is (may differ if nested)
    git_toplevel, _ = run_git(["rev-parse", "--show-toplevel"], SCRIPT_DIR)
    print(f"git toplevel: {git_toplevel}")
    if os.path.normcase(git_toplevel) != os.path.normcase(repo_root):
        print(f"\nWARNING: git resolves to a PARENT repo ({git_toplevel}).")
        print("We will use the script directory directly.")

    # ------------------------------------------------------------------ #
    # 2. Does the branch exist?
    # ------------------------------------------------------------------ #
    section(f"2. Branch exists? → {branch}")
    all_branches, _ = run_git(["branch", "-a"], repo_root)
    print("All branches:")
    print(all_branches or "(none — repo has no commits yet)")

    _, code = run_git(["rev-parse", "--verify", branch], repo_root)
    if code != 0:
        print(f"\nERROR: branch '{branch}' does NOT exist in {repo_root}.")
        print("\nMost likely causes:")
        print("  A) bridge.py's REPO_PATH points somewhere else.")
        print(f"     Check agent/.env → REPO_PATH should be: {repo_root}")
        print("  B) The bridge commit step failed before creating the branch.")
        print("     Re-run: python bridge.py --flow combined ... and look for:")
        print("       [bridge] Creating branch ...")
        print("       [bridge] Wrote entity file ...")
        print("       [bridge] Committing ...")
        sys.exit(1)

    print(f"\nBranch '{branch}' exists. ✓")

    # ------------------------------------------------------------------ #
    # 3. All files on that branch
    # ------------------------------------------------------------------ #
    section(f"3. All files on '{branch}'")
    all_files, _ = run_git(["ls-tree", "-r", "--name-only", branch], repo_root)
    if not all_files:
        print("Branch has NO committed files.")
        print("→ Bridge ran but commit step failed.")
        sys.exit(1)
    print(all_files)

    # ------------------------------------------------------------------ #
    # 4. Entity JSON files
    # ------------------------------------------------------------------ #
    section(f"4. Entity JSON files under {ENTITY_DIRS} on '{branch}'")
    entity_files = []
    for d in ENTITY_DIRS:
        out, _ = run_git(["ls-tree", "-r", "--name-only", branch, d], repo_root)
        for line in out.splitlines():
            line = line.strip()
            if line.endswith(".json"):
                entity_files.append(line)

    if not entity_files:
        print(f"No .json files under {ENTITY_DIRS} on '{branch}'.")
        print("All files on branch:")
        print(all_files)
        sys.exit(1)

    print(f"Found {len(entity_files)} entity file(s):")
    for f in entity_files:
        print(f"  {f}")

    # ------------------------------------------------------------------ #
    # 5. Read contents via git show
    # ------------------------------------------------------------------ #
    section("5. File contents via 'git show'")
    for path in entity_files:
        content, code = run_git(["show", f"{branch}:{path}"], repo_root)
        if code != 0:
            print(f"  ERROR reading {path}: {content}")
            continue
        try:
            parsed = json.loads(content)
            print(f"\n  FILE: {path}")
            print(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            print(f"  FILE: {path}  (not valid JSON)")
            print(content[:500])

    # ------------------------------------------------------------------ #
    # 6. Check git_context.py version
    # ------------------------------------------------------------------ #
    section("6. agent/git_context.py version check")
    gc_path = os.path.join(AGENT_DIR, "git_context.py")
    if not os.path.exists(gc_path):
        print(f"ERROR: {gc_path} not found.")
        sys.exit(1)

    with open(gc_path) as f:
        source = f.read()

    checks = {
        "ENTITY_DIRS constant":    "ENTITY_DIRS" in source,
        "_format_entity_contents": "_format_entity_contents" in source,
        "Scans all branches":      "branch_names if b" in source,
    }
    all_ok = True
    for label, ok in checks.items():
        status = "✓ present" if ok else "✗ MISSING — still the OLD version"
        print(f"  {label:30s}: {status}")
        if not ok:
            all_ok = False

    if not all_ok:
        print("\n→ Replace agent/git_context.py with the updated version from outputs.")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 7. Live context output
    # ------------------------------------------------------------------ #
    section("7. Live gather_repo_context — ENTITY FILE CONTENTS section")
    sys.path.insert(0, AGENT_DIR)
    spec = importlib.util.spec_from_file_location("git_context", gc_path)
    gc_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gc_mod)

    ctx = gc_mod.gather_repo_context(repo_root, user_query=f"show valuetypes in branch {branch}")
    summary = ctx["summary"]

    marker = "ENTITY FILE CONTENTS"
    idx = summary.find(marker)
    if idx == -1:
        print("ERROR: 'ENTITY FILE CONTENTS' section missing from summary.")
        print("Last 500 chars of summary:")
        print(summary[-500:])
        sys.exit(1)

    print(summary[idx:idx + 2000])
    print("\n✓ Entity contents present in context — agent will answer correctly.")


if __name__ == "__main__":
    main()
