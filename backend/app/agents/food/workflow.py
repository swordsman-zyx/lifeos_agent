import sys
from pathlib import Path

from langgraph.graph import END, START, StateGraph

if __name__ == "__main__" and not __package__:
    # Support both IDE "Run file" and `python -m backend.app.agents.food.workflow`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    __package__ = "backend.app.agents.food"

from .context import FoodContext
from .nodes import approval, assistant, cart, query_tools, resolve_order, submit_order
from .state import FoodState


def next_step(state: FoodState) -> str:
    return state["route"]


def build_graph(checkpointer):
    graph = StateGraph(FoodState, context_schema=FoodContext)
    graph.add_node("assistant", assistant)
    graph.add_node("query_tools", query_tools)
    graph.add_node("cart", cart)
    graph.add_node("approval", approval)
    graph.add_node("submit_order", submit_order)
    graph.add_node("resolve_order", resolve_order)
    graph.add_edge(START, "assistant")
    graph.add_conditional_edges(
        "assistant",
        next_step,
        {
            "assistant": "assistant",
            "query_tools": "query_tools",
            "cart": "cart",
            "resolve_order": "resolve_order",
            "end": END,
        },
    )
    graph.add_edge("query_tools", "assistant")
    graph.add_conditional_edges(
        "cart", next_step, {"assistant": "assistant", "approval": "approval"}
    )
    graph.add_conditional_edges("approval", next_step, {"submit_order": "submit_order", "end": END})
    graph.add_conditional_edges(
        "submit_order", next_step, {"resolve_order": "resolve_order", "end": END}
    )
    graph.add_edge("resolve_order", END)
    return graph.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    drawable = build_graph(checkpointer=None).get_graph()
    mermaid = drawable.draw_mermaid()
    print(mermaid)

    output_dir = Path(__file__).resolve().parents[4] / "docs"
    output_dir.mkdir(parents=True, exist_ok=True)
    mermaid_path = output_dir / "food-workflow.mmd"
    mermaid_path.write_text(mermaid, encoding="utf-8")
    print(f"Mermaid 流程图已保存：{mermaid_path}")

    # LangGraph's default PNG renderer uses mermaid.ink and requires internet access.
    image_path = output_dir / "food-workflow.png"
    try:
        drawable.draw_mermaid_png(output_file_path=str(image_path), max_retries=0)
        print(f"PNG 流程图已保存：{image_path}")
    except Exception as exc:
        print(f"PNG 渲染失败，请检查网络连接。Mermaid 文件仍可查看。原因：{exc}")
