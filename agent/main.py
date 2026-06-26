from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

load_dotenv()

from graph import app


def _print_result(result: dict):
    if result.get("final_answer"):
        print(f"\n{result['final_answer']}\n")
        return

    if result.get("done"):
        print("\n=== TASK FINISHED ===\n")
        return

    if result.get("waiting_for_user"):
        print(result.get("question", "Additional input required."))


def run_agent_query(user_input: str, max_steps: int = 15) -> dict:
    """Run the agent workflow for a single user query and return final state."""
    state = {
        "messages": [HumanMessage(content=user_input)],
        "done": False,
        "last_return_code": 0,
        "last_output": "",
        "execution_history": [],
        "next_command": "",
        "original_request": user_input,
        "observer_result": "NONE",
        "replan": False,
        "human_response": "",
        "waiting_for_user": False,
        "todo_complete": False,
        "question": "",
        "final_answer": "",
        "repo_context": "",
        "default_branch": "",
    }

    steps = 0
    while steps < max_steps:
        steps += 1
        result = app.invoke(state)

        _print_result(result)

        if result.get("done") and not result.get("waiting_for_user"):
            return result

        if result.get("waiting_for_user"):
            # In non-interactive mode we stop and return the waiting state.
            return result

        state = result

    # Max steps reached; return current state
    return state


def run_agent_loop():
    while True:
        user_input = input("You: ").strip() #here the jira ticket is being passed to the agent as a user input. The agent will then process this input and provide a response based on its workflow.

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit", "q"}:
            break

        state = {
            "messages": [HumanMessage(content=user_input)],
            "done": False,
            "last_return_code": 0,
            "last_output": "",
            "execution_history": [],
            "next_command": "",
            "original_request": user_input,
            "observer_result": "NONE",
            "replan": False,
            "human_response": "",
            "waiting_for_user": False,
            "todo_complete": False,
            "question": "",
            "final_answer": "",
            "repo_context": "",
            "default_branch": "",
        }

        while True:
            result = app.invoke(state)

            _print_result(result)

            if result.get("done") and not result.get("waiting_for_user"):
                break

            if result.get("waiting_for_user"):
                answer = input("> ").strip()
                state = {
                    **result,
                    "messages": result["messages"] + [HumanMessage(content=answer)],
                    "human_response": answer,
                    "waiting_for_user": False,
                    "replan": True,
                    "done": False,
                }
                continue

            state = result


if __name__ == "__main__":
    run_agent_loop()
