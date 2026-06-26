import re
import shlex
import subprocess

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

from config import GROQ_MODEL, REPO_PATH
from state import GitState

load_dotenv()

llm = ChatGroq(model=GROQ_MODEL)

OBSERVER_PROMPT = SystemMessage(
    content="""
You are a GitOps Execution Reviewer.

You receive the current TODO, the command that ran, and its output.
Decide whether that command fully completed the TODO.

Rules:
- Judge only whether THIS command achieved THIS TODO.
- TODO is complete only if the command succeeded and the goal was met.
- Finding information (hash, branch list, status) completes when output contains it.
- Creating a branch completes only on successful creation.
- Cherry-pick completes when cherry-pick succeeds (no conflict).
- If cherry-pick reports conflict, TODO is NOT complete.
- If the branch already exists, TODO is NOT complete.

Output format:

TODO_COMPLETE: YES|NO

ANALYSIS:
<one or two sentences>
"""
)

def _current_branch() -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _last_command(state: GitState) -> str:
    command = state.get("next_command", "").strip()
    if command:
        return command

    if state.get("execution_history"):
        latest = state["execution_history"][-1]
        for line in latest.splitlines():
            if line.startswith("COMMAND: "):
                return line.removeprefix("COMMAND: ").strip()

    return ""


def _checkout_target(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None

    if len(parts) < 3 or parts[0] != "git" or parts[1] not in {"checkout", "switch"}:
        return None

    args = parts[2:]
    if "--" in args:
        return None

    create_flags = {"-b", "-B", "-c", "-C", "--create", "--force-create"}
    for idx, arg in enumerate(args):
        if arg in create_flags and idx + 1 < len(args):
            return args[idx + 1]

    value_flags = {"--track", "--orphan", "--detach"}
    positionals = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in value_flags:
            idx += 2
            continue
        if arg.startswith("-"):
            idx += 1
            continue
        positionals.append(arg)
        idx += 1

    if len(positionals) == 1:
        return positionals[0]

    return None


def _delete_target(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None

    if len(parts) < 4 or parts[:2] != ["git", "branch"]:
        return None

    delete_flags = {"-d", "-D", "--delete"}
    args = parts[2:]
    for idx, arg in enumerate(args):
        if arg in delete_flags and idx + 1 < len(args):
            return args[idx + 1]

    return None


def _deterministic_completion(state: GitState) -> tuple[bool, str] | None:
    last_return_code = state.get("last_return_code", 0)
    last_output = state.get("last_output", "") or ""

    command = _last_command(state)

    delete_target = _delete_target(command)
    if delete_target:
        # If branch is already gone according to git, consider the TODO complete.
        if not _branch_exists(delete_target):
            return True, f"Git no longer finds branch '{delete_target}'."

        # If git returned a non-zero exit but output indicates "not found", treat as complete.
        if last_return_code != 0 and re.search(r"not.*found|not a valid.*ref|fatal:.*not", last_output, re.IGNORECASE):
            return True, f"Delete command returned non-zero, but output indicates branch '{delete_target}' is absent."

        return False, f"Delete command {'succeeded' if last_return_code == 0 else 'failed'}; branch '{delete_target}' still exists."

    target = _checkout_target(command)
    if not target:
        return None

    current = _current_branch()
    if current == target:
        return True, f"Git reports the current branch is '{current}'."

    # If output contains explicit switch messages, accept that as completion when reasonable.
    if last_return_code != 0:
        if re.search(r"Switched to|Already on|HEAD is now at", last_output, re.IGNORECASE):
            return True, f"Checkout output indicates switch to '{target}'."
        return None

    return False, f"Checkout command succeeded, but current branch is '{current or '(detached HEAD)'}', not '{target}'."


def observer_node(state: GitState):
    todos = state.get("todos", [])
    current_idx = state.get("current_todo", 0)

    if current_idx >= len(todos):
        return {"done": True}

    current_todo = todos[current_idx]
    latest_message = state["messages"][-1].content if state.get("messages") else ""

    deterministic = _deterministic_completion(state)
    if deterministic is not None:
        todo_complete, analysis = deterministic
        review = (
            f"TODO_COMPLETE: {'YES' if todo_complete else 'NO'}\n\n"
            f"ANALYSIS:\n{analysis}"
        )

        print("\n===== OBSERVER =====")
        print(f"TODO: {current_todo}")
        print(review)

        return {
            "review": review,
            "todo_complete": todo_complete,
            "done": False,
        }

    observation_input = f"""
CURRENT TODO:
{current_todo}

LAST RETURN CODE: {state.get("last_return_code", "unknown")}

{latest_message}
"""

    response = llm.invoke(
        [OBSERVER_PROMPT, HumanMessage(content=observation_input)]
    )

    review = response.content.strip()

    print("\n===== OBSERVER =====")
    print(f"TODO: {current_todo}")
    print(review)

    todo_complete = "TODO_COMPLETE: YES" in review.upper()

    return {
            "review": review,
            "todo_complete": todo_complete,
            "done": False,
    }
