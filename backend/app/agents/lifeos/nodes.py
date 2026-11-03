import asyncio
import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ..food.nodes import answer
from .context import LifeOSContext
from .prompts import ROUTER_PROMPT
from .state import LifeOSState, RouteDecision

logger = logging.getLogger(__name__)


async def route_request(state: LifeOSState, runtime: Runtime[LifeOSContext]):
    if state.get("refresh_only") or state.get("selection"):
        return {"intent": "food", "route": "read_preferences"}
    history = [
        {"role": "user" if isinstance(m, HumanMessage) else "assistant", "content": str(m.content)}
        for m in state.get("messages", [])
        if isinstance(m, (HumanMessage, AIMessage)) and not getattr(m, "tool_calls", [])
    ][-8:]
    try:
        raw = await runtime.context.llm.with_structured_output(
            RouteDecision, method="function_calling"
        ).ainvoke(
            [
                SystemMessage(content=ROUTER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "history": history,
                            "food_active": state.get("food_active", False),
                            "message": state["user_message"],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        decision = RouteDecision.model_validate(raw)
    except Exception:
        logger.exception("LifeOS routing failed")
        return answer(state, "模型服务暂时不可用，请稍后重试。", error="MODEL_UNAVAILABLE")
    if decision.intent == "unsupported":
        return answer(state, decision.reply or "我目前可以帮你点外卖，以及管理饮食偏好。")
    return {
        "intent": decision.intent,
        "route": "manage_memory"
        if decision.intent in {"memory", "memory_food"}
        else "read_preferences",
    }


def make_memory_node(memory_graph):
    async def manage_memory(state: LifeOSState, config, runtime: Runtime[LifeOSContext]):
        result = await memory_graph.ainvoke(
            {
                "mode": "message",
                "source_id": "message:" + state["request_id"],
                "message": state["user_message"],
            },
            config=config,
            context=runtime.context,
        )
        text = result["reply"]
        updates = {
            "dietary_preferences": result["preferences"],
            "memory_reply": text,
            "messages": [AIMessage(content=text, id=state["request_id"] + ":memory")],
        }
        if state["intent"] == "memory_food":
            return {**updates, "route": "read_preferences"}
        return {**updates, "reply": text, "status": "idle", "route": "end"}

    return manage_memory


async def read_preferences(state: LifeOSState, runtime: Runtime[LifeOSContext]):
    if state.get("refresh_only"):
        return {"dietary_preferences": state.get("dietary_preferences", [])}
    return {"dietary_preferences": runtime.context.memory.preferences()}


def make_food_node(food_graph):
    async def call_food(state: LifeOSState, config, runtime: Runtime[LifeOSContext]):
        # Direct subgraph invocation: interrupts propagate to the parent runtime.
        inputs = {
            "messages": state.get("messages", []),
            "session_id": state["session_id"],
            "request_id": state["request_id"],
            "selection": state.get("selection"),
            "selected_candidate": next(
                (
                    candidate
                    for candidate in (state.get("candidates") or [])
                    if state.get("selection")
                    and candidate["kind"] == state["selection"]["kind"]
                    and candidate["id"] == state["selection"]["id"]
                ),
                None,
            ),
            "candidate_request_id": None,
            "candidate_pool": [],
            "candidate_pool_request_id": None,
            "dietary_preferences": state.get("dietary_preferences", []),
            "tool_rounds": 0,
            "tool_calls": [],
            "error": None,
            "refresh_only": state.get("refresh_only", False),
            "status": "processing",
        }
        if not state.get("refresh_only"):
            inputs.update(cart=None, approval=None, order_result=None)
        if state.get("selection") and state["selection"]["kind"] == "address":
            inputs["address_id"] = state["selection"]["id"]
        result = await food_graph.ainvoke(inputs, config=config, context=runtime.context)
        updates = {
            key: result.get(key)
            for key in (
                "reply",
                "status",
                "cart",
                "approval",
                "order_result",
                "address_id",
                "candidates",
                "error",
            )
        }
        updates["messages"] = [
            m
            for m in result.get("messages", [])
            if isinstance(m, (AIMessage, HumanMessage)) and not getattr(m, "tool_calls", [])
        ]
        updates.update(food_active=not bool(result.get("order_result")), refresh_only=False)
        return updates

    return call_food


async def record_order(state: LifeOSState, runtime: Runtime[LifeOSContext]):
    await asyncio.to_thread(
        runtime.context.orders.save, state["session_id"], state.get("order_result") or {}
    )
    return {}


def make_learning_node(memory_graph):
    async def learn_orders(state: LifeOSState, config, runtime: Runtime[LifeOSContext]):
        try:
            for order in runtime.context.orders.pending_learning():
                await memory_graph.ainvoke(
                    {"mode": "order", "source_id": "order:" + order["order_id"], "order": order},
                    config=config,
                    context=runtime.context,
                )
        except Exception:
            # The paid order was already committed by record_order. Leave learning pending.
            logger.exception("Order memory learning unavailable; order result is preserved")
        return {}

    return learn_orders
