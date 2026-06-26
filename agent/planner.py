import re

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

from config import GROQ_MODEL
from git_context import gather_repo_context
from state import GitState

load_dotenv()

llm = ChatGroq(model=GROQ_MODEL)


def _enhance_todos_with_auto_merge(todos: list[str], default_branch: str) -> list[str]:
    """Auto-append merge todos after branch creation."""
    enhanced = []
    
    for todo in todos:
        enhanced.append(todo)
        # Detect "Create branch X" patterns
        match = re.search(r"Create\s+branch\s+([^\s.,;:!?()]+)", todo, re.IGNORECASE)
        if match:
            branch_name = match.group(1).strip()
            if branch_name != default_branch:
                merge_todo = f"Merge {branch_name} into {default_branch}"
                enhanced.append(merge_todo)
    
    return enhanced


def _build_planner_prompt(repo_rules: str) -> SystemMessage:
    return SystemMessage(
        content=f"""
You are a GitOps Task Planner.

You receive a rich repository snapshot: branches, numbered commits, files changed
per commit, file-to-commit map, branch heads, and divergence info.

Use this context directly. Do NOT plan discovery todos for data already in context.

Repository rules:
{repo_rules}

Your job:
1. For git operations: break the request into atomic, ordered todos.
2. For informational questions: answer directly from context.

RULES:
- Output ONLY in one of the formats below.
- Do NOT output shell commands.
- Do NOT output markdown fences.
- Each action todo maps to exactly one git command.
- Include commit hashes in todos when they appear in context.
- Commit #N on branch X = the #N entry under branch X in the commit index.
- For selective multi-branch work: plan create-branch then cherry-picks in order.
- Cherry-pick oldest commit first when order matters.
- Do not cherry-pick commits the user did not ask for.

FORMAT FOR OPERATIONS:

TODOS:
1. <todo>
2. <todo>

FORMAT FOR INFORMATIONAL QUESTIONS:

ANSWER:
<clear, accurate answer>

EXAMPLES:

User: create branch release from main, cherry-pick commit 2 from feature-a and commit 1 from hotfix
Context shows main tip abc1234, feature-a #2 def5678, hotfix #1 ghi9012

TODOS:
1. Create branch release at abc1234 (main tip)
2. Cherry-pick def5678 (commit #2 on feature-a) onto release
3. Cherry-pick ghi9012 (commit #1 on hotfix) onto release

User: which commit on developer added config.py?

ANSWER:
Use the FILE to COMMIT MAP and commit index from context.

User: create branch rett from developer commit 3

TODOS:
1. Create branch rett at the hash for commit #3 on developer (from context)
"""
    )


def extract_todos(text: str) -> list[str]:
    todos = []
    in_todos = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TODOS:"):
            in_todos = True
            continue
        if stripped.upper().startswith("ANSWER:"):
            break
        if in_todos and re.match(r"^\d+\.", stripped):
            todos.append(re.sub(r"^\d+\.\s*", "", stripped))

    return todos


def extract_answer(text: str) -> str | None:
    match = re.search(r"ANSWER:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def looks_like_direct_answer(text: str) -> bool:
    if not text.strip():
        return False

    if re.search(r"^\s*TODOS:\b|^\s*ANSWER:\b", text, re.IGNORECASE | re.MULTILINE):
        return False
    if re.search(r"\bgit\s+(checkout|branch|cherry-pick|merge|rebase|reset|push|pull|fetch)\b", text, re.IGNORECASE):
        return False

    numbered_items = re.findall(r"^\s*\d+\.\s*(.+)$", text, re.MULTILINE)
    if numbered_items:
        short_items = [item.strip() for item in numbered_items if item.strip()]
        if short_items and all(len(item.split()) <= 5 for item in short_items):
            if not any(re.search(r"\b(create|cherry-pick|branch|commit|merge|delete|checkout)\b", item, re.IGNORECASE) for item in short_items):
                return True

    return True


def _is_delete_approval(response: str) -> bool:
    normalized = response.strip().lower()
    if normalized in {"y", "yes", "delete", "approve", "approved", "proceed", "ok", "okay"}:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "delete it",
            "delete the branch",
            "yes delete",
            "go ahead",
            "proceed with delete",
        )
    )


def _extract_branch_name(text: str) -> str | None:
    patterns = (
        r"branch\s+['\"]([^'\"]+)['\"]",
        r"delete\s+(?:the\s+)?branch\s+([^\s.,;:!?]+)",
        r"branch\s+([^\s.,;:!?]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _approved_delete_todo(state: GitState) -> str | None:
    if not state.get("replan") or not _is_delete_approval(state.get("human_response", "")):
        return None

    question = state.get("question", "")
    original_request = state.get("original_request", "")
    combined = f"{question}\n{original_request}"
    if "delete" not in combined.lower():
        return None

    branch = _extract_branch_name(question) or _extract_branch_name(original_request)
    if not branch:
        return None

    return f"Delete branch {branch} (explicitly approved by user)"


def planner_node(state: GitState):
    user_request = (
        state["original_request"]
        if state.get("replan")
        else state["messages"][-1].content
    )
    context = gather_repo_context(user_query=user_request)
    repo_rules = context["repo_rules"]

    approved_delete_todo = _approved_delete_todo(state)
    if approved_delete_todo:
        response = HumanMessage(content=f"TODOS:\n1. {approved_delete_todo}")
        print("\n===== PLANNER =====")
        print(response.content)
        print("\n===== TODOS =====")
        print([approved_delete_todo])

        return {
            "messages": [response],
            "todos": [approved_delete_todo],
            "current_todo": 0,
            "done": False,
            "final_answer": "",
            "repo_context": context["summary"],
            "default_branch": context["default_branch"],
            "replan": False,
            "human_response": "",
            "execution_history": state.get("execution_history", []),
        }

    if state.get("replan"):
        user_content = f"""
The workflow was interrupted and replanning is required.

Original Request:
{state["original_request"]}

User Correction / Update:
{state["human_response"]}

Generate a new plan using the updated request.
"""
    else:
        user_content = state["messages"][-1].content

    messages = [
        _build_planner_prompt(repo_rules),
        HumanMessage(
            content=f"""
REPOSITORY CONTEXT:
{context["summary"]}

USER REQUEST:
{user_content}
"""
        ),
    ]

    response = llm.invoke(messages)

    print("\n===== PLANNER =====")
    print(response.content)

    content = response.content.strip()
    answer = extract_answer(content)
    if answer:
        print("\n===== ANSWER =====")
        print(answer)
        return {
            "messages": [response],
            "todos": [],
            "current_todo": 0,
            "done": True,
            "final_answer": answer,
            "repo_context": context["summary"],
            "default_branch": context["default_branch"],
            "replan": False,
            "human_response": "",
        }

    todos = extract_todos(content)

    print("\n===== TODOS =====")
    print(todos)

    if not todos:
        if looks_like_direct_answer(content):
            return {
                "messages": [response],
                "todos": [],
                "current_todo": 0,
                "done": True,
                "final_answer": content,
                "repo_context": context["summary"],
                "default_branch": context["default_branch"],
                "replan": False,
                "human_response": "",
            }
        raise ValueError(f"Planner produced invalid output:\n{content}")

    # Auto-enhance todos with merge-after-create
    # Skip if any todo is a cherry-pick — merging after cherry-pick is wrong
    has_cherry_pick = any(
        "cherry-pick" in todo.lower() for todo in todos
    )
    enhanced_todos = todos if has_cherry_pick else _enhance_todos_with_auto_merge(todos, context["default_branch"])
    
    if enhanced_todos != todos:
        print("\n===== AUTO-MERGED TODOS (auto-merge enabled) =====")
        print(enhanced_todos)

    return {
        "messages": [response],
        "todos": enhanced_todos,
        "current_todo": 0,
        "done": False,
        "final_answer": "",
        "repo_context": context["summary"],
        "default_branch": context["default_branch"],
        "replan": False,
        "human_response": "",
        "execution_history": state.get("execution_history", []),
    }