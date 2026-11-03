from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langchain_core.messages.tool import ToolCall
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class FoodState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    status: str
    route: str
    request_id: str
    session_id: str
    dietary_preferences: list[dict[str, Any]]
    reply: str
    address_id: str
    candidates: list[dict[str, Any]]
    candidate_request_id: str | None
    candidate_pool: list[dict[str, Any]]
    candidate_pool_request_id: str | None
    selection: dict[str, str] | None
    selected_candidate: dict[str, Any] | None
    tool_calls: list[ToolCall]
    tool_rounds: int
    cart: dict[str, Any] | None
    approval: dict[str, Any] | None
    order_result: dict[str, Any] | None
    refresh_only: bool
    error: str | None


class ApprovalDecision(BaseModel):
    approval_id: str = Field(min_length=1)
    action: Literal["confirm", "cancel"]


class Selection(BaseModel):
    kind: Literal["food", "restaurant", "address"]
    id: str = Field(min_length=1)
