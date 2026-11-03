import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from .prompts import EXTRACT_PROMPT, ORDER_PROMPT
from .state import MemoryDecision, MemoryState, OrderPreferences

logger = logging.getLogger(__name__)


def describe(preferences):
    if not preferences:
        return "目前没有已保存的饮食偏好。"
    return "\n".join(
        f"• {p['subject']}：{'不喜欢' if p['polarity'] == 'dislike' else '偏好'}{p['value']}（{'你明确表达' if p['source'] == 'explicit' else str(p['evidence_count']) + '笔成功订单推断'}）"
        for p in preferences
    )


async def process_memory(state: MemoryState, runtime: Runtime) -> dict:
    context = runtime.context
    repo = context.memory
    source_id = state["source_id"]
    previous = repo.event(source_id)
    if previous and previous["status"] == "done":
        return {
            "preferences": repo.preferences(),
            "reply": describe(repo.preferences()),
            "error": None,
        }
    try:
        if state["mode"] == "order":
            raw = await context.llm.with_structured_output(
                OrderPreferences, method="function_calling"
            ).ainvoke(
                [
                    SystemMessage(content=ORDER_PROMPT),
                    HumanMessage(content=json.dumps(state["order"], ensure_ascii=False)),
                ]
            )
            result = OrderPreferences.model_validate(raw)
            await asyncio.to_thread(
                repo.record_order,
                state["order"]["order_id"],
                [p.model_dump() for p in result.preferences if p.value and p.polarity == "like"],
            )
            return {"preferences": repo.preferences(), "reply": "", "error": None}
        current = repo.preferences()
        raw = await context.llm.with_structured_output(
            MemoryDecision, method="function_calling"
        ).ainvoke(
            [
                SystemMessage(
                    content=EXTRACT_PROMPT
                    + "\n已知偏好："
                    + json.dumps(current, ensure_ascii=False)
                ),
                HumanMessage(content=state["message"]),
            ]
        )
        decision = MemoryDecision.model_validate(raw)
        operations = [
            op.model_dump()
            for op in decision.operations
            if op.action == "forget" or op.value.strip()
        ]
        await asyncio.to_thread(repo.apply_explicit, source_id, operations)
        preferences = repo.preferences()
        reply = (
            ("已更新饮食偏好。\n" if operations else "") + describe(preferences)
            if decision.query or operations
            else "本次没有需要保存的长期饮食偏好。"
        )
        return {"preferences": preferences, "reply": reply, "error": None}
    except Exception:
        logger.exception("Memory processing failed")
        await asyncio.to_thread(repo.fail, source_id, state["mode"], "模型提取或记忆保存失败")
        return {
            "preferences": repo.preferences(),
            "reply": "饮食记忆暂时未能更新，已保留原有偏好，请稍后重试。",
            "error": "MEMORY_UNAVAILABLE",
        }
