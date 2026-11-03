import asyncio
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from ...core.tracing import graph_config
from .context import FoodContext
from .state import ApprovalDecision


class FoodAgentService:
    """Public entry point. Run the local API with one worker for per-session serialization."""

    def __init__(self, graph, repository, context: FoodContext):
        self.graph, self.repository = graph, repository
        self.context = context
        self.locks = {}

    def lock(self, sid):
        return self.locks.setdefault(sid, asyncio.Lock())

    def config(self, sid):
        return graph_config({"configurable": {"thread_id": sid}, "recursion_limit": 48})

    async def _recover(self, sid):
        config = self.config(sid)
        snapshot = await self.graph.aget_state(config)
        if snapshot.next:
            interrupted = any(task.interrupts for task in snapshot.tasks)
            if interrupted:
                approval = snapshot.values.get("approval")
                saved = self.repository.approval(approval["id"]) if approval else None
                if saved and saved["status"] != "pending":
                    await self.graph.ainvoke(
                        Command(
                            resume={
                                "approval_id": saved["id"],
                                "action": "confirm" if saved["status"] == "confirmed" else "cancel",
                            }
                        ),
                        config,
                        context=self.context,
                    )
            else:
                # Resume an unfinished node after process termination, keeping the original key.
                await self.graph.ainvoke(None, config, context=self.context)

    async def _view(self, sid):
        self.repository.get(sid)
        snapshot = await self.graph.aget_state(self.config(sid))
        state = snapshot.values
        for message in state.get("messages", []):
            if (
                isinstance(message, (AIMessage, HumanMessage))
                and message.content
                and not getattr(message, "tool_calls", [])
            ):
                self.repository.add_message(
                    sid,
                    message.id,
                    "user" if isinstance(message, HumanMessage) else "assistant",
                    str(message.content),
                )
        approval = state.get("approval")
        if approval:
            approval = self.repository.approval(approval["id"])
        return {
            "session": self.repository.get(sid),
            "session_id": sid,
            "status": state.get("status", "idle"),
            "error": state.get("error"),
            "messages": self.repository.messages(sid),
            "candidates": (state.get("candidates") or []),
            "cart": state.get("cart"),
            "approval": {"id": approval["id"], "status": approval["status"]} if approval else None,
            "order_result": state.get("order_result"),
            "request_reference": approval["idempotency_key"] if approval else None,
        }

    async def get_session(self, sid):
        self.repository.get(sid)
        async with self.lock(sid):
            await self._recover(sid)
            return await self._view(sid)

    async def chat(self, sid, message, client_message_id, selection=None):
        self.repository.get(sid)
        async with self.lock(sid):
            await self._recover(sid)
            config = self.config(sid)
            old = (await self.graph.aget_state(config)).values
            payload = {"message": message, "selection": selection}
            previous = self.repository.request(sid, client_message_id, payload)
            if previous and (previous["completed"] or old.get("request_id") == client_message_id):
                self.repository.complete(sid, client_message_id)
                return await self._view(sid)
            if old.get("status") == "awaiting_confirmation":
                raise ValueError("请先通过订单卡片确认或取消当前订单")
            if old.get("status") == "order_pending":
                raise ValueError("当前订单结果待确认，请先查询订单状态")
            if selection and not any(
                c["kind"] == selection["kind"] and c["id"] == selection["id"]
                for c in (old.get("candidates") or [])
            ):
                raise ValueError("该候选不属于当前会话，请刷新后重新选择")
            self.repository.add_message(sid, client_message_id, "user", message)
            await self.graph.ainvoke(
                {
                    "messages": [HumanMessage(content=message, id=client_message_id)],
                    "request_id": client_message_id,
                    "selection": selection,
                    "selected_candidate": next(
                        (
                            candidate
                            for candidate in (old.get("candidates") or [])
                            if selection
                            and candidate["kind"] == selection["kind"]
                            and candidate["id"] == selection["id"]
                        ),
                        None,
                    ),
                    "candidate_request_id": None,
                    "candidate_pool": [],
                    "candidate_pool_request_id": None,
                    "address_id": selection["id"]
                    if selection and selection["kind"] == "address"
                    else old.get("address_id", ""),
                    "tool_rounds": 0,
                    "tool_calls": [],
                    "error": None,
                    "status": "processing",
                    "reply": "",
                    "cart": None,
                    "approval": None,
                    "order_result": None,
                },
                config,
                context=self.context,
            )
            self.repository.complete(sid, client_message_id)
            return await self._view(sid)

    async def resume(self, sid, decision: ApprovalDecision):
        self.repository.get(sid)
        async with self.lock(sid):
            snapshot = await self.graph.aget_state(self.config(sid))
            current = snapshot.values.get("approval")
            if not current or current["id"] != decision.approval_id:
                raise ValueError("该订单确认卡片已失效")
            saved = self.repository.approval(decision.approval_id)
            desired = "confirmed" if decision.action == "confirm" else "cancelled"
            if saved["status"] != "pending" and saved["status"] != desired:
                raise ValueError("该订单已作出其他决定")
            if snapshot.next:
                if any(task.interrupts for task in snapshot.tasks):
                    self.repository.decide(sid, decision.approval_id, decision.action)
                    await self.graph.ainvoke(
                        Command(resume=decision.model_dump()),
                        self.config(sid),
                        context=self.context,
                    )
                else:
                    await self._recover(sid)
            elif saved["status"] == "pending":
                raise ValueError("当前没有等待恢复的人工确认")
            return await self._view(sid)

    async def refresh_order(self, sid):
        self.repository.get(sid)
        async with self.lock(sid):
            await self._recover(sid)
            old = (await self.graph.aget_state(self.config(sid))).values
            if old.get("status") != "order_pending":
                return await self._view(sid)
            await self.graph.ainvoke(
                {
                    "refresh_only": True,
                    "request_id": uuid.uuid4().hex,
                    "tool_rounds": 0,
                    "selection": None,
                },
                self.config(sid),
                context=self.context,
            )
            return await self._view(sid)
