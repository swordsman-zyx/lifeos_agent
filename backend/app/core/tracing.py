"""Official LangChain tracing; local usage is kept separately from cloud traces."""

import logging
from contextvars import ContextVar

from langchain_core.tools import StructuredTool
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client

from shared.config import settings

from .redaction import clean
from .usage import UsageCallback

logger = logging.getLogger(__name__)
trace_scope = ContextVar("trace_scope", default=None)


def create_trace_client():
    if not settings.langsmith_tracing:
        return None
    key = settings.langsmith_api_key.get_secret_value().strip()
    if not key:
        raise ValueError("LANGSMITH_TRACING=true 时必须配置 LANGSMITH_API_KEY")
    return Client(
        api_url=settings.langsmith_endpoint,
        api_key=key,
        timeout_ms=5000,
        hide_inputs=clean,
        hide_outputs=clean,
        hide_metadata=clean,
        run_ops_buffer_size=100,
        run_ops_buffer_timeout_ms=1000,
        process_buffered_run_ops=lambda operations: [clean(op) for op in operations],
    )


def request_config(usage, client, trace_id, session_id, operation_id):
    callbacks = [UsageCallback(usage)]
    if client is not None:
        callbacks.append(LangChainTracer(client=client, project_name=settings.langsmith_project))
    return {
        "run_id": trace_id,
        "run_name": "LifeOS.request",
        "callbacks": callbacks,
        "metadata": {
            "trace_id": str(trace_id),
            "session_id": session_id,
            "usage_operation_id": operation_id,
        },
        "tags": ["lifeos"],
    }


def graph_config(config):
    scope = trace_scope.get()
    if scope:
        # Inherit the callback manager from the outer Runnable, including its
        # parent run ID. Replacing it with a new list would split the trace tree.
        config = {**config, "metadata": scope}
    return config


async def traced_tool(name, arguments, attempt, operation):
    if trace_scope.get() is None:
        return await operation

    async def invoke(arguments: dict, attempt: int):
        return await operation

    tool = StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description="外卖 MCP 工具的一次实际调用",
    )
    return await tool.ainvoke(
        {"arguments": arguments, "attempt": attempt},
        config={"metadata": {"mcp_attempt": attempt}, "tags": ["mcp"]},
    )
