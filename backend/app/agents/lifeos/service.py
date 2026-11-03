import asyncio
import logging
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from ...core.tracing import graph_config
from ..food.service import FoodAgentService
from ..food.state import ApprovalDecision
from .context import LifeOSContext

logger = logging.getLogger(__name__)


def interrupted_payload(snapshot):
    for task in snapshot.tasks:
        for item in task.interrupts:
            if isinstance(item.value, dict) and item.value.get("type") == "order_confirmation":
                return item.value
    return None


class LifeOSService:
    def __init__(self, graph, repository, context: LifeOSContext, legacy: FoodAgentService):
        self.graph, self.repository, self.context, self.legacy = graph, repository, context, legacy
        self.locks = {}

    def lock(self, sid):
        return self.locks.setdefault(sid, asyncio.Lock())

    def config(self, sid):
        return graph_config({"configurable": {"thread_id": "lifeos:" + sid}, "recursion_limit": 64})

    async def legacy_pending(self, sid):
        old = await self.legacy.graph.aget_state(self.legacy.config(sid))
        return bool(
            old.next or old.values.get("status") in {"awaiting_confirmation", "order_pending"}
        )

    async def _recover(self, sid):
        snapshot = await self.graph.aget_state(self.config(sid))
        if not snapshot.next:
            return
        payload = interrupted_payload(snapshot)
        if payload:
            saved = self.repository.approval(payload["approval_id"])
            if saved and saved["status"] != "pending":
                await self.graph.ainvoke(
                    Command(
                        resume={
                            "approval_id": saved["id"],
                            "action": "confirm" if saved["status"] == "confirmed" else "cancel",
                        }
                    ),
                    self.config(sid),
                    context=self.context,
                )
        else:
            await self.graph.ainvoke(None, self.config(sid), context=self.context)

    def _persist_card(self, sid, state):
        preview, approval = state.get("cart"), state.get("approval")
        if not preview or not approval:
            return
        saved = self.repository.approval(approval["id"])
        if not saved or saved["session_id"] != sid:
            raise ValueError("订单确认事项不属于当前会话")
        request_id = state.get("request_id")
        anchor = request_id + ":preview" if request_id else "order:" + approval["id"]
        # Reuse the original preview message during refreshes; never move a card to a new query.
        existing = self.context.orders.card_anchor(approval["id"])
        anchor = existing or anchor
        self.repository.add_message(
            sid, anchor, "assistant", "请核对订单信息，确认后才会提交并模拟支付。"
        )
        payload = {
            "id": saved["id"],
            "cart": preview,
            "approval": {"id": saved["id"], "status": saved["status"]},
            "order_result": state.get("order_result"),
            "request_reference": saved["idempotency_key"],
            "dietary_preferences": state.get("dietary_preferences", []),
        }
        self.context.orders.card(sid, anchor, saved["id"], payload)
        self.context.orders.save(sid, state.get("order_result") or {})

    async def _view(self, sid):
        snapshot = await self.graph.aget_state(self.config(sid))
        state = dict(snapshot.values)
        payload = interrupted_payload(snapshot)
        if payload:
            state.update(
                cart=payload["cart"],
                approval={"id": payload["approval_id"]},
                status="awaiting_confirmation",
                request_id=payload["request_id"],
                order_result=None,
                candidates=[],
                dietary_preferences=payload.get("dietary_preferences", []),
            )
        for message in state.get("messages", []):
            if (
                isinstance(message, (HumanMessage, AIMessage))
                and message.content
                and not getattr(message, "tool_calls", [])
            ):
                self.repository.add_message(
                    sid,
                    message.id,
                    "user" if isinstance(message, HumanMessage) else "assistant",
                    str(message.content),
                )
        self._persist_card(sid, state)
        approval = (
            self.repository.approval(state["approval"]["id"]) if state.get("approval") else None
        )
        return {
            "session": self.repository.get(sid),
            "session_id": sid,
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "messages": self.context.orders.attach_cards(sid, self.repository.messages(sid)),
            "candidates": (state.get("candidates") or []),
            "cart": state.get("cart"),
            "approval": {"id": approval["id"], "status": approval["status"]} if approval else None,
            "order_result": state.get("order_result"),
            "request_reference": approval["idempotency_key"] if approval else None,
            "dietary_preferences": state.get("dietary_preferences", []),
            "food_active": state.get("food_active", False),
        }

    async def _legacy_view(self, sid, operation=None):
        view = await (operation if operation is not None else self.legacy.get_session(sid))
        snapshot = await self.legacy.graph.aget_state(self.legacy.config(sid))
        self._persist_card(sid, {**snapshot.values, **view})
        return {
            **view,
            "messages": self.context.orders.attach_cards(sid, self.repository.messages(sid)),
            "dietary_preferences": [],
            "legacy": True,
        }

    async def get_session(self, sid):
        self.repository.get(sid)
        async with self.lock(sid):
            snapshot = await self.graph.aget_state(self.config(sid))
            if await self.legacy_pending(sid) or not snapshot.values:
                old = await self.legacy.graph.aget_state(self.legacy.config(sid))
                if old.values:
                    return await self._legacy_view(sid)
            await self._recover(sid)
            return await self._view(sid)

    async def chat(self, sid, message, client_message_id, selection=None):
        self.repository.get(sid)
        async with self.lock(sid):
            if await self.legacy_pending(sid):
                raise ValueError("请先完成旧订单的确认、取消或状态查询")
            await self._recover(sid)
            snapshot = await self.graph.aget_state(self.config(sid))
            old = snapshot.values
            payload = {"message": message, "selection": selection}
            previous = self.repository.request(sid, client_message_id, payload)
            if previous and (previous["completed"] or old.get("request_id") == client_message_id):
                self.repository.complete(sid, client_message_id)
                return await self._view(sid)
            if interrupted_payload(snapshot):
                raise ValueError("请先确认或取消当前订单")
            if old.get("status") == "order_pending":
                raise ValueError("请先查询当前订单结果，避免重复下单")
            if selection and not any(
                c["kind"] == selection["kind"] and c["id"] == selection["id"]
                for c in (old.get("candidates") or [])
            ):
                raise ValueError("该候选已失效，请重新选择")
            self.repository.add_message(sid, client_message_id, "user", message)
            history = [
                HumanMessage(content=m["content"], id=m["id"])
                if m["role"] == "user"
                else AIMessage(content=m["content"], id=m["id"])
                for m in self.repository.messages(sid)
            ]
            await self.graph.ainvoke(
                {
                    "messages": history,
                    "session_id": sid,
                    "request_id": client_message_id,
                    "user_message": message,
                    "selection": selection,
                    "tool_calls": [],
                    "error": None,
                    "status": "processing",
                    "cart": None,
                    "approval": None,
                    "order_result": None,
                    "memory_reply": "",
                    "refresh_only": False,
                },
                self.config(sid),
                context=self.context,
            )
            self.repository.complete(sid, client_message_id)
            return await self._view(sid)

    async def resume(self, sid, decision: ApprovalDecision):
        self.repository.get(sid)
        async with self.lock(sid):
            if await self.legacy_pending(sid):
                return await self._legacy_view(sid, self.legacy.resume(sid, decision))
            snapshot = await self.graph.aget_state(self.config(sid))
            payload = interrupted_payload(snapshot)
            current = (
                payload["approval_id"]
                if payload
                else (snapshot.values.get("approval") or {}).get("id")
            )
            if current != decision.approval_id:
                raise ValueError("订单确认卡片已失效")
            self.repository.decide(sid, current, decision.action)
            if payload:
                await self.graph.ainvoke(
                    Command(resume=decision.model_dump()), self.config(sid), context=self.context
                )
            elif snapshot.next:
                await self._recover(sid)
            return await self._view(sid)

    async def refresh_order(self, sid):
        self.repository.get(sid)
        async with self.lock(sid):
            if await self.legacy_pending(sid):
                return await self._legacy_view(sid, self.legacy.refresh_order(sid))
            await self._recover(sid)
            state = (await self.graph.aget_state(self.config(sid))).values
            if state.get("status") == "order_pending":
                await self.graph.ainvoke(
                    {"refresh_only": True, "request_id": uuid.uuid4().hex, "selection": None},
                    self.config(sid),
                    context=self.context,
                )
            return await self._view(sid)

    async def recent_orders(self, limit=10):
        for record in self.context.orders.backfill_candidates():
            try:
                result = await self.context.client.call(
                    "get_order", {"idempotency_key": record["idempotency_key"]}
                )
                self.context.orders.save(record["session_id"], result)
            except Exception:
                logger.warning("Historical order sync unavailable")
        return {"orders": self.context.orders.recent(limit)}
