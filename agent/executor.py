import re
import subprocess

from langchain_core.messages import HumanMessage

from config import REPO_PATH
from state import GitState


def extract_command(text: str) -> str | None:
    match = re.search(
        r"NEXT_COMMAND:\s*(.+)",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return match.group(1).strip().splitlines()[0].strip()


def _append_history(state: GitState, command: str, output: str, return_code: int) -> list[str]:
    history = list(state.get("execution_history", []))
    history.append(
        f"COMMAND: {command}\n"
        f"EXIT CODE: {return_code}\n"
        f"OUTPUT:\n{output or '(empty)'}"
    )
    return history


def executor_node(state: GitState):
    command = state.get("next_command", "").strip()
    if not command and state.get("messages"):
        command = extract_command(state["messages"][-1].content) or ""

    if not command:
        error = "ERROR: No NEXT_COMMAND found."
        return {
            "messages": [HumanMessage(content=error)],
            "last_output": error,
            "last_return_code": 1,
            "next_command": "",
        }

    print("\n===== EXECUTING =====")
    print(command)

    result = subprocess.run(
        command,
        cwd=REPO_PATH,
        shell=True,
        capture_output=True,
        text=True,
    )

    output = result.stdout.strip()
    if not output:
        output = result.stderr.strip()

    print("\n===== OUTPUT =====")
    print(output)

    history = _append_history(state, command, output, result.returncode)

    return {
        "messages": [
            HumanMessage(
                content=(
                    f"COMMAND:\n{command}\n\n"
                    f"EXIT CODE: {result.returncode}\n\n"
                    f"OUTPUT:\n{output or '(empty)'}"
                )
            )
        ],
        "last_output": output,
        "last_return_code": result.returncode,
        "execution_history": history,
        "next_command": "",
    }
