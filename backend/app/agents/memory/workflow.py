from langgraph.graph import END, START, StateGraph

from .nodes import process_memory
from .state import MemoryState


def build_graph():
    builder = StateGraph(MemoryState)
    builder.add_node("process_memory", process_memory)
    builder.add_edge(START, "process_memory")
    builder.add_edge("process_memory", END)
    return builder.compile()
