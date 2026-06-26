import re

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

from config import GROQ_MODEL, REPO_PATH
from state import GitState

load_dotenv()

llm = ChatGroq(model=GROQ_MODEL)


def _build_coordinator_prompt(default_branch: str) -> SystemMessage:
    return SystemMessage(
        content=f"""
You are a GitOps Workflow Coordinator.

You translate the current TODO into exactly one safe git shell command.
You have full repository context (commits, files, branch tips) and execution history.

Default branch: {default_branch}
Repository path: {REPO_PATH}

Responsibilities:
1. Trust the Observer review for whether the current TODO is complete.
2. Emit the single best next git command for the current TODO.
3. Reuse commit hashes and branch names from context or execution history.
4. Request human input only when intent is ambiguous or action is destructive.

CONTEXT RULES:
- Scan ALL execution history before generating a command.
- Never re-run a command whose output already satisfies the current TODO.
- Commit #N on branch X = #N in the commit index for branch X.
- Use exact hashes from context for branch create and cherry-pick operations.
- Cherry-pick format: git cherry-pick <hash>
- Create branch format: git branch <name> <hash>  OR  git checkout -b <name> <hash>

SELECTIVE BRANCH BUILDING:
- Create the base branch first from the specified tip hash.
- Cherry-pick only the commits listed in the plan, one per command, in order.
- If a cherry-pick conflicts, stop and request human input.

COMMAND RULES:
- Output ONLY git commands (pipes to grep/head/tail allowed when needed).
- Never output echo, printf, or shell-only commands.
- Never guess commit hashes.
- One command per response.

PROHIBITED (require HUMAN_INPUT_REQUIRED):
- Delete branches
- Force push
- History rewrite (rebase -i, reset --hard, etc.)
- Modifying an existing branch without explicit user approval

- Do not delete branches unless the user explicitly approves deletion after a prompt.

If a requested branch already exists and the TODO is to create it:
HUMAN_INPUT_REQUIRED:
Branch '<name>' already exists. Use a different name, switch to it, or delete it?

If the user responds with explicit approval to delete the existing branch, output the required delete command using `git branch -D <name>`.

GENERATION RULES:
- Always create branch first, then create ValueType, cherry-pick in order.
- If the current TODO is already satisfied, output a comment and do not re-run the command      

OUTPUT FORMAT (exactly one):

NEXT_COMMAND:
<single git command>

OR

HUMAN_INPUT_REQUIRED:
<question>
"""
    )

def extract_next_command(text: str) -> str | None:
    match = re.search(r"NEXT_COMMAND:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().splitlines()[0].strip()


def _approved_delete_command(todo: str) -> str | None:
    if "explicitly approved by user" not in todo.lower():
        return None

    match = re.search(r"\bdelete\s+branch\s+([^\s.,;:!?()]+)", todo, re.IGNORECASE)
    if not match:
        return None

    branch = match.group(1).strip()
    return f"git branch -D {branch}"
def coordinator_node(state: GitState):
    human_response = state.get("human_response", "")
    if human_response:
        return {
            "replan": True,
            "waiting_for_user": False,
            "todo_complete": False,
            "current_todo": 0,
            "todos": [],
        }

    todos = state.get("todos", [])
    current_idx = state.get("current_todo", 0)

    if state.get("todo_complete", False):
        current_idx += 1
        if current_idx >= len(todos):
            return {
                "current_todo": current_idx,
                "done": True,
                "todo_complete": False,
            }

    if current_idx >= len(todos):
        return {"done": True}

    current_todo = todos[current_idx]
    default_branch = state.get("default_branch", "main")
    history = state.get("execution_history", [])
    history_text = (
        "\n\n---\n\n".join(history)
        if history
        else "(no commands executed yet)"
    )

    approved_delete_command = _approved_delete_command(current_todo)
    if approved_delete_command:
        content = f"NEXT_COMMAND:\n{approved_delete_command}"

        print("\n===== COORDINATOR =====")
        print(f"TODO: {current_todo}")
        print(content)

        return {
            "messages": [HumanMessage(content=content)],
            "current_todo": current_idx,
            "todo_complete": False,
            "next_command": approved_delete_command,
        }

    history_text = (
        "\n\n---\n\n".join(history)
        if history
        else "(no commands executed yet)"
    )

    todo_context = SystemMessage(
        content=f"""
REPOSITORY CONTEXT:
{state.get("repo_context", "(not loaded)")}

CURRENT TODO:
{current_todo}

TODO PROGRESS:
{current_idx + 1}/{len(todos)}

LAST COMMAND OUTPUT:
{state.get("last_output", "(none)")}

EXECUTION HISTORY:
{history_text}

OBSERVER REVIEW:
{state.get("review", "NONE")}
"""
    )

    messages = [
        _build_coordinator_prompt(default_branch),
        todo_context,
        HumanMessage(content=f"Original user request: {state.get('original_request', '')}"),
    ]

    response = llm.invoke(messages)
    content = response.content.strip()

    if content.upper().startswith("HUMAN_INPUT_REQUIRED:"):
        question = re.sub(
            r"^HUMAN_INPUT_REQUIRED:\s*",
            "",
            content,
            flags=re.IGNORECASE,
        ).strip()

        print("\n===== HUMAN INPUT REQUIRED =====")
        print(question)

        return {
            "waiting_for_user": True,
            "question": question,
            "current_todo": current_idx,
            "todo_complete": False,
        }

    command = extract_next_command(content)
    if not command:
        raise ValueError(f"Coordinator produced no command:\n{content}")

    print("\n===== COORDINATOR =====")
    print(f"TODO: {current_todo}")
    print(content)

    return {
        "messages": [response],
        "current_todo": current_idx,
        "todo_complete": False,
        "next_command": command,
    }
