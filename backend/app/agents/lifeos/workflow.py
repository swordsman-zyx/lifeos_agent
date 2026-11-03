import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "backend.app.agents.lifeos"

from ..food.workflow import build_graph as build_food_graph
from ..memory.workflow import build_graph as build_memory_graph
from .context import LifeOSContext
from .nodes import (
    make_food_node,
    make_learning_node,
    make_memory_node,
    read_preferences,
    record_order,
    route_request,
)
from .state import LifeOSState


def next_step(state):
    return state["route"]


def build_graph(checkpointer):
    food = build_food_graph(checkpointer=True)
    memory = build_memory_graph()
    graph = StateGraph(LifeOSState, context_schema=LifeOSContext)
    graph.add_node("route_request", route_request)
    graph.add_node("manage_memory", make_memory_node(memory))
    graph.add_node("read_preferences", read_preferences)
    graph.add_node("warm_memory", make_learning_node(memory))
    graph.add_node("food", make_food_node(food))
    graph.add_node("record_order", record_order)
    graph.add_node("learn_orders", make_learning_node(memory))
    graph.add_edge(START, "route_request")
    graph.add_conditional_edges(
        "route_request",
        next_step,
        {"manage_memory": "manage_memory", "read_preferences": "warm_memory", "end": END},
    )
    graph.add_conditional_edges(
        "manage_memory", next_step, {"read_preferences": "warm_memory", "end": END}
    )
    graph.add_edge("warm_memory", "read_preferences")
    graph.add_edge("read_preferences", "food")
    graph.add_edge("food", "record_order")
    graph.add_edge("record_order", "learn_orders")
    graph.add_edge("learn_orders", END)
    return graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    diagram = build_graph(None).get_graph(xray=True).draw_mermaid()
    print(diagram)
    destination = Path(__file__).resolve().parents[4] / "docs" / "lifeos-workflow.mmd"
    destination.write_text(diagram, encoding="utf-8")
    print(f"流程图已保存：{destination}")
