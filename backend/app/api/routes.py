import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context
from pydantic import BaseModel, Field

from ..agents.food.state import ApprovalDecision, Selection
from ..core.tracing import request_config, trace_scope
from ..core.usage import usage_scope

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    client_message_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    selection: Selection | None = None


class ResumeRequest(ApprovalDecision):
    session_id: str


async def safe_call(operation):
    try:
        return await operation
    except KeyError as exc:
        raise HTTPException(404, "会话不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("Agent operation failed")
        raise HTTPException(503, "服务暂时不可用。保留当前会话，稍后刷新即可重试或恢复。") from exc


@router.get("/health")
async def health(request: Request):
    client = request.app.state.mcp
    return {
        "status": "ok",
        "mcp_tools": sorted(client.definitions),
        "langsmith_enabled": request.app.state.tracing_client is not None,
    }


@router.post("/sessions")
async def create_session(request: Request):
    session = request.app.state.repository.create()
    return await tracked(
        request,
        session["id"],
        uuid4().hex,
        request.app.state.service.get_session(session["id"]),
        explicit=False,
    )


@router.get("/sessions")
async def list_sessions(request: Request):
    return {"sessions": request.app.state.repository.list()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return await tracked(
        request,
        session_id,
        uuid4().hex,
        request.app.state.service.get_session(session_id),
        explicit=False,
    )


@router.post("/food/chat")
async def chat(body: ChatRequest, request: Request):
    return await tracked(
        request,
        body.session_id,
        "chat:" + body.client_message_id,
        request.app.state.legacy_service.chat(
            body.session_id,
            body.message,
            body.client_message_id,
            body.selection.model_dump() if body.selection else None,
        ),
    )


@router.post("/food/resume")
async def resume(body: ResumeRequest, request: Request):
    return await tracked(
        request,
        body.session_id,
        "resume:" + body.approval_id + ":" + body.action,
        request.app.state.legacy_service.resume(
            body.session_id, ApprovalDecision(approval_id=body.approval_id, action=body.action)
        ),
    )


@router.post("/food/sessions/{session_id}/order/refresh")
async def refresh(session_id: str, request: Request):
    return await tracked(
        request,
        session_id,
        "refresh:" + uuid4().hex,
        request.app.state.legacy_service.refresh_order(session_id),
    )


@router.post("/lifeos/chat")
async def lifeos_chat(body: ChatRequest, request: Request):
    return await tracked(
        request,
        body.session_id,
        "chat:" + body.client_message_id,
        request.app.state.service.chat(
            body.session_id,
            body.message,
            body.client_message_id,
            body.selection.model_dump() if body.selection else None,
        ),
    )


@router.post("/lifeos/resume")
async def lifeos_resume(body: ResumeRequest, request: Request):
    return await tracked(
        request,
        body.session_id,
        "resume:" + body.approval_id + ":" + body.action,
        request.app.state.service.resume(
            body.session_id, ApprovalDecision(approval_id=body.approval_id, action=body.action)
        ),
    )


@router.post("/lifeos/sessions/{session_id}/order/refresh")
async def lifeos_refresh(session_id: str, request: Request):
    return await tracked(
        request,
        session_id,
        "refresh:" + uuid4().hex,
        request.app.state.service.refresh_order(session_id),
    )


@router.get("/orders")
async def orders(request: Request, limit: int = Query(default=10, ge=1, le=50)):
    return await safe_call(request.app.state.service.recent_orders(limit))


@router.get("/usage")
async def usage(request: Request):
    return request.app.state.usage.account()


async def tracked(request, session_id, operation_id, operation, *, explicit=True):
    repository = request.app.state.usage
    operation_id = session_id + ":" + operation_id
    before = {m["id"] for m in request.app.state.repository.messages(session_id)}
    if explicit:
        repository.begin(operation_id, session_id)
    token = usage_scope.set((operation_id, session_id))
    trace_id = str(uuid4())
    inputs = await request.json() if request.method == "POST" else {"session_id": session_id}
    scope_token = trace_scope.set(
        {"trace_id": trace_id, "session_id": session_id, "usage_operation_id": operation_id}
    )
    config = request_config(
        repository, request.app.state.tracing_client, trace_id, session_id, operation_id
    )
    logger.info(
        "Agent request started trace_id=%s session_id=%s path=%s",
        trace_id,
        session_id,
        request.url.path,
    )
    try:

        async def execute(_inputs):
            return await safe_call(operation)

        # Supply the masked client to inherited SDK contexts too. The manager
        # reuses the explicit LangChainTracer rather than adding a second one.
        with tracing_context(
            enabled=request.app.state.tracing_client is not None,
            client=request.app.state.tracing_client,
        ):
            view = await RunnableLambda(execute).ainvoke(inputs, config=config)
        messages = [
            m for m in view["messages"] if m["role"] == "assistant" and m["id"] not in before
        ]
        repository.finish_operation(operation_id, messages[-1]["id"] if messages else None)
        logger.info("Agent request finished trace_id=%s session_id=%s", trace_id, session_id)
        view["trace_id"] = trace_id
        return repository.decorate(view)
    finally:
        trace_scope.reset(scope_token)
        usage_scope.reset(token)
        repository.finish_operation(operation_id)
