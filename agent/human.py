from state import GitState
from langchain_core.messages import HumanMessage


def human_node(state: GitState):

    print("\n===== HUMAN INPUT REQUIRED =====")
    print(state["question"])

    answer = input("\n> ")

    return {
        "human_response": answer,
        "waiting_for_user": False,
    }

