import asyncio
from contextvars import ContextVar

from langchain_core.callbacks import AsyncCallbackHandler

usage_scope = ContextVar("usage_scope", default=None)


class UsageCallback(AsyncCallbackHandler):
    run_inline = True

    def __init__(self, repository):
        self.repository = repository

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        metadata = metadata or {}
        scope = usage_scope.get()
        if scope is None and metadata.get("usage_operation_id"):
            scope = (metadata["usage_operation_id"], metadata["session_id"])
        if scope is None:
            return
        metadata = metadata or {}
        node = metadata.get("langgraph_node", "")
        agent = {"route_request": "lifeos", "assistant": "food", "process_memory": "memory"}.get(
            node, "other"
        )
        await asyncio.to_thread(
            self.repository.start_call, str(run_id), *scope, agent, metadata.get("ls_model_name")
        )

    async def on_llm_end(self, response, *, run_id, **kwargs):
        usage = None
        for group in response.generations:
            for generation in group:
                usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
                if usage:
                    break
            if usage:
                break
        if not usage:
            raw = (response.llm_output or {}).get("token_usage") or {}
            if raw:
                usage = {
                    "input_tokens": raw.get("prompt_tokens"),
                    "output_tokens": raw.get("completion_tokens"),
                }
        await asyncio.to_thread(self.repository.finish_call, str(run_id), usage)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        await asyncio.to_thread(self.repository.finish_call, str(run_id))
