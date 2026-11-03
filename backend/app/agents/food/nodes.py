import asyncio
import json
import logging
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from .context import FoodContext
from .prompts import SYSTEM_PROMPT
from .state import FoodState
from .tools import MODEL_TOOLS

logger = logging.getLogger(__name__)


class RecommendedItem(BaseModel):
    id: str = Field(description="本轮搜索结果中的餐厅 ID 或餐品 ID")
    reason: str = Field(description="简短推荐理由，不编造价格或配送信息")


class PresentCandidates(BaseModel):
    """向用户展示本轮搜索结果中的餐厅或餐品候选。"""

    kind: Literal["restaurant", "food"]
    items: list[RecommendedItem] = Field(min_length=1, max_length=3)


def present_candidates(state: FoodState, args: dict) -> tuple[list[dict], str] | None:
    try:
        choice = PresentCandidates.model_validate(args)
    except Exception:
        return None
    if state.get("candidate_pool_request_id") != state.get("request_id"):
        return None
    pool = {
        (item["kind"], item["id"]): item for item in state.get("candidate_pool", [])
    }
    ids = [item.id for item in choice.items]
    if len(ids) != len(set(ids)) or any((choice.kind, item_id) not in pool for item_id in ids):
        return None
    cards = [
        {
            **pool[(choice.kind, item.id)],
            "description": pool[(choice.kind, item.id)]["description"]
            + (" · " + item.reason if item.reason else ""),
        }
        for item in choice.items
    ]
    label = "家餐厅" if choice.kind == "restaurant" else "款餐品"
    return cards, f"为你找到以下 {len(cards)} {label}，请查看卡片并选择。"


def recommendation_answer(state: FoodState, response: AIMessage, result) -> dict:
    cards, reply = result
    messages = [response]
    for call in response.tool_calls:
        messages.append(ToolMessage(content=reply, tool_call_id=call["id"]))
    messages.append(AIMessage(content=reply, id=state["request_id"] + ":recommendation"))
    return {"messages": messages, "reply": reply, "candidates": cards,
            "candidate_request_id": state["request_id"], "status": "idle", "route": "end"}


async def retry_recommendation(state: FoodState, runtime: Runtime[FoodContext]) -> dict:
    if not state.get("candidate_pool"):
        return answer(state, "没有找到符合条件的餐厅或餐品，请换个条件再试。", candidates=[])
    try:
        choice = await runtime.context.llm.with_structured_output(
            PresentCandidates, method="function_calling"
        ).ainvoke([
            SystemMessage(content="仅从本轮工具搜索结果中选最多三个候选，返回其实际 ID 和简短理由。"),
            *state.get("messages", []),
        ])
        result = present_candidates(state, choice.model_dump())
    except Exception:
        logger.exception("Structured recommendation failed")
        result = None
    if result:
        return recommendation_answer(state, AIMessage(content=""), result)
    return answer(state, "暂时无法生成可靠的候选推荐，请换个条件再试。", candidates=[])


def answer(state: FoodState, text: str, **updates) -> dict:
    """Operational messages for limits, approval and order results, not intent parsing."""
    return {
        "reply": text,
        "status": "idle",
        "route": "end",
        "messages": [AIMessage(content=text, id=state["request_id"] + ":answer")],
        **updates,
    }


async def assistant(state: FoodState, runtime: Runtime[FoodContext]) -> dict:
    """Let the model interpret the conversation and choose its next tool call."""
    if state.get("refresh_only"):
        return {"route": "resolve_order", "refresh_only": False}
    if state.get("tool_rounds", 0) >= 12:
        return answer(state, "本轮查询较多，已停止。请补充想选的餐厅、餐品或规格后继续。")

    model = runtime.context.llm.bind_tools(
        [*runtime.context.client.model_definitions(), PresentCandidates]
    )
    preferences = [
        {
            key: preference[key]
            for key in ("dimension", "subject", "value", "polarity", "source")
            if key in preference
        }
        for preference in state.get("dietary_preferences", [])
    ]
    system = SYSTEM_PROMPT + "\n当前饮食偏好：" + json.dumps(preferences, ensure_ascii=False)
    selected = state.get("selected_candidate")
    selection_message = (
        [
            HumanMessage(
                content="本轮用户点击选择的候选："
                + json.dumps(
                    {key: selected[key] for key in ("kind", "id", "name") if key in selected},
                    ensure_ascii=False,
                )
            )
        ]
        if selected
        else []
    )
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=system),
                *state.get("messages", []),
                *selection_message,
            ]
        )
    except Exception:
        logger.exception("Model invocation failed")
        return answer(
            state, "模型服务暂时不可用，请检查模型配置或稍后重试。", error="MODEL_UNAVAILABLE"
        )

    if not response.tool_calls:
        if state.get("candidate_pool_request_id") == state.get("request_id"):
            return await retry_recommendation(state, runtime)
        if state.get("candidate_request_id") == state.get("request_id") and state.get("candidates"):
            return answer(state, "已找到以下配送地址，请点击卡片选择。")
        return {
            "messages": [response],
            "reply": str(response.content),
            "status": "idle",
            "route": "end",
        }

    names = [call["name"] for call in response.tool_calls]
    error = None
    if names == ["PresentCandidates"]:
        result = present_candidates(state, response.tool_calls[0]["args"])
        if result:
            return recommendation_answer(state, response, result)
        return await retry_recommendation(state, runtime)
    if "PresentCandidates" in names:
        error = "推荐候选必须单独调用。"
    elif any(name not in MODEL_TOOLS for name in names):
        error = "该工具不能由模型调用。下单必须通过人工确认后的专用节点。"
    elif "set_cart" in names and len(names) != 1:
        error = "set_cart 必须单独调用，请先完成查询再设置购物车。"
    if error:
        return {
            "messages": [
                response,
                *[
                    ToolMessage(content=error, tool_call_id=call["id"])
                    for call in response.tool_calls
                ],
            ],
            "route": "assistant",
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }
    return {
        "messages": [response],
        "route": "cart" if names == ["set_cart"] else "query_tools",
        "tool_calls": response.tool_calls,
    }


def candidates_from_result(name: str, data: dict) -> list[dict] | None:
    """Adapt platform data for UI cards without interpreting user intent."""
    if name == "get_menu":
        return [
            {
                "kind": "food",
                "id": f["food_id"],
                "name": f["name"],
                "price": f["base_price"],
                "description": f["description"],
            }
            for f in data["foods"]
        ]
    if name == "search_restaurants":
        return [
            {
                "kind": "restaurant",
                "id": r["restaurant_id"],
                "name": r["name"],
                "description": f"评分 {r['rating']} · 距离 {r['distance_meters']} 米 · 配送费 ¥{r['delivery_fee'] / 100:.2f}",
            }
            for r in data["restaurants"]
        ]
    if name == "list_delivery_addresses":
        return [
            {
                "kind": "address",
                "id": a["address_id"],
                "name": a["address"],
                "description": a["contact"] + " " + a["phone"],
            }
            for a in data["addresses"]
        ]
    return None


async def query_tools(state: FoodState, runtime: Runtime[FoodContext]) -> dict:
    messages, candidates = [], state.get("candidates", [])
    candidate_request_id = state.get("candidate_request_id")
    candidate_pool = state.get("candidate_pool", [])
    candidate_pool_request_id = state.get("candidate_pool_request_id")
    address_id = state.get("address_id", "")
    for call in state["tool_calls"]:
        try:
            data = await runtime.context.client.call(call["name"], call["args"])
            cards = candidates_from_result(call["name"], data)
            if cards is not None:
                if call["name"] == "list_delivery_addresses":
                    candidates = cards
                    candidate_request_id = state["request_id"]
                    candidate_pool, candidate_pool_request_id = [], None
                else:
                    candidates = []
                    candidate_pool, candidate_pool_request_id = cards, state["request_id"]
            elif call["name"] == "get_food_details":
                candidate_pool, candidate_pool_request_id = [], None
            if call["name"] == "list_delivery_addresses":
                address_id = address_id or data["default_address_id"]
            content = json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            content = json.dumps({"error": str(exc)}, ensure_ascii=False)
        messages.append(ToolMessage(content=content, tool_call_id=call["id"]))
    return {
        "messages": messages,
        "tool_rounds": state.get("tool_rounds", 0) + 1,
        "candidates": candidates,
        "candidate_request_id": candidate_request_id,
        "candidate_pool": candidate_pool,
        "candidate_pool_request_id": candidate_pool_request_id,
        "address_id": address_id,
    }


async def cart(state: FoodState, config: RunnableConfig, runtime: Runtime[FoodContext]) -> dict:
    call = state["tool_calls"][0]
    try:
        preview = await runtime.context.client.call("set_cart", call["args"])
    except Exception as exc:
        return {
            "route": "assistant",
            "messages": [
                ToolMessage(
                    content=json.dumps({"error": str(exc)}, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            ],
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }
    approval_record = await asyncio.to_thread(
        runtime.context.repository.create_approval,
        state.get("session_id") or config["configurable"]["thread_id"],
        preview,
    )
    text = "已整理好订单，请核对配送地址、规格和金额，再选择确认下单或取消。"
    return {
        "messages": [
            ToolMessage(content=json.dumps(preview, ensure_ascii=False), tool_call_id=call["id"]),
            AIMessage(content=text, id=state["request_id"] + ":preview"),
        ],
        "reply": text,
        "cart": preview,
        "approval": approval_record,
        "status": "awaiting_confirmation",
        "route": "approval",
        "candidates": [],
        "order_result": None,
        "address_id": preview["delivery_address"]["address_id"],
    }


async def approval(state: FoodState, config: RunnableConfig, runtime: Runtime[FoodContext]) -> dict:
    record = state["approval"]
    decision = interrupt(
        {
            "type": "order_confirmation",
            "approval_id": record["id"],
            "cart": state["cart"],
            "actions": ["confirm", "cancel"],
            "request_id": state["request_id"],
            "dietary_preferences": state.get("dietary_preferences", []),
        }
    )
    if decision.get("approval_id") != record["id"] or decision.get("action") not in {
        "confirm",
        "cancel",
    }:
        raise ValueError("人工确认参数无效")
    record = await asyncio.to_thread(
        runtime.context.repository.decide,
        state.get("session_id") or config["configurable"]["thread_id"],
        record["id"],
        decision["action"],
    )
    if decision["action"] == "cancel":
        return answer(
            state,
            "已取消，本次没有提交订单或支付。",
            approval=record,
            order_result={"outcome": "cancelled"},
        )
    return {"approval": record, "status": "processing", "route": "submit_order"}


async def submit_order(state: FoodState, runtime: Runtime[FoodContext]) -> dict:
    record = runtime.context.repository.approval(state["approval"]["id"])
    if not record or record["status"] != "confirmed":
        raise ValueError("订单尚未获得人工确认")
    preview = state["cart"]
    try:
        result = await runtime.context.client.call(
            "submit_order",
            {
                "cart_id": preview["cart_id"],
                "cart_version": preview["cart_version"],
                "idempotency_key": record["idempotency_key"],
            },
        )
    except Exception:
        result = {"outcome": "unknown", "message": "提交响应未确认。"}
    if result["outcome"] in {"pending", "unknown"}:
        return {"order_result": result, "status": "order_pending", "route": "resolve_order"}
    return order_answer(state, result)


def order_answer(state: FoodState, result: dict) -> dict:
    if result["outcome"] == "succeeded":
        order = result["order"]
        text = f"下单并模拟支付成功。订单号：{order['order_id']}，合计 ¥{order['cart']['amounts']['total'] / 100:.2f}。"
    elif result["outcome"] == "rejected":
        text = "下单未完成：" + (result.get("message") or "平台明确返回失败。")
    else:
        text = "订单结果暂未确认，请不要重复下单。可以点击“查询订单状态”继续核实。"
    return answer(
        state,
        text,
        order_result=result,
        status="order_pending" if result["outcome"] in {"pending", "unknown"} else "idle",
        candidates=[],
    )


async def resolve_order(state: FoodState, runtime: Runtime[FoodContext]) -> dict:
    record = runtime.context.repository.approval(state["approval"]["id"])
    result = state.get("order_result") or {"outcome": "unknown"}
    for delay in (0, *runtime.context.retry_delays):
        if delay:
            await asyncio.sleep(delay)
        try:
            result = await runtime.context.client.call(
                "get_order", {"idempotency_key": record["idempotency_key"]}
            )
        except Exception:
            result = {"outcome": "unknown", "message": "暂时无法查询。"}
        if result["outcome"] in {"succeeded", "rejected"}:
            break
    return order_answer(state, result)
