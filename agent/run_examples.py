"""Run a few non-interactive agent examples for smoke testing."""

import sys
import traceback

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

load_dotenv()

from git_context import gather_repo_context
from graph import app


def make_state(query: str) -> dict:
    return {
        "messages": [HumanMessage(content=query)],
        "done": False,
        "last_return_code": 0,
        "last_output": "",
        "execution_history": [],
        "next_command": "",
        "original_request": query,
        "replan": False,
        "human_response": "",
        "waiting_for_user": False,
        "todo_complete": False,
        "question": "",
        "final_answer": "",
        "repo_context": "",
        "default_branch": "",
    }


def run_query(query: str, max_steps: int = 15) -> dict:
    state = make_state(query)
    steps = 0

    while steps < max_steps:
        steps += 1
        state = app.invoke(state)

        if state.get("done") and not state.get("waiting_for_user"):
            return state

        if state.get("waiting_for_user"):
            return {
                **state,
                "stopped_reason": f"needs human input: {state.get('question', '')}",
            }

    return {**state, "stopped_reason": "max steps reached"}


def print_section(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def main():
    print_section("TEST 1: Git context gathering (no LLM)")
    ctx = gather_repo_context(user_query="main branches commits")
    print(f"Valid repo: {ctx['valid']}")
    print(f"Default branch: {ctx['default_branch']}")
    idx = ctx["summary"].find("COMMIT INDEX")
    if idx >= 0:
        print(ctx["summary"][idx : idx + 500])
    else:
        print(ctx["summary"][:500])

    examples = [
        "what branches exist in this repo?",
        "which commit on main added planner.py?",
        "show the last 3 commits on main with their files",
    ]

    for i, query in enumerate(examples, start=2):
        print_section(f"TEST {i}: {query}")
        try:
            result = run_query(query)
            if result.get("final_answer"):
                print("RESULT (direct answer):")
                print(result["final_answer"])
            elif result.get("stopped_reason"):
                print(f"STOPPED: {result['stopped_reason']}")
                if result.get("execution_history"):
                    print("Commands run:")
                    for entry in result["execution_history"]:
                        first_line = entry.splitlines()[0]
                        print(f"  - {first_line}")
            elif result.get("done"):
                print("RESULT: task finished")
                if result.get("execution_history"):
                    print("Commands run:")
                    for entry in result["execution_history"]:
                        first_line = entry.splitlines()[0]
                        print(f"  - {first_line}")
            else:
                print("RESULT: incomplete")
                print("Todos:", result.get("todos"))
        except Exception as exc:
            print("FAILED:", exc)
            traceback.print_exc()
            return 1

    print_section("ALL EXAMPLES COMPLETED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
