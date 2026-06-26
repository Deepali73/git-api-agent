from typing import TypedDict
from langchain_core.messages import BaseMessage


class GitState(TypedDict, total=False):
    messages: list[BaseMessage]

    todos: list[str]
    current_todo: int

    done: bool
    final_answer: str

    last_return_code: int
    last_output: str
    execution_history: list[str]
    next_command: str

    repo_context: str
    default_branch: str

    review: str
    todo_complete: bool

    waiting_for_user: bool
    question: str
    replan: bool
    original_request: str
    human_response: str
