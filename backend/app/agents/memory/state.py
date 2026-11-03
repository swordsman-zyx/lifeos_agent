from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class Preference(BaseModel):
    dimension: Literal["food", "cuisine", "ingredient", "flavor", "sweetness", "temperature"]
    subject: str = Field(
        min_length=1,
        max_length=80,
        description="规范化主体；甜度和温度通常用“饮品”，食材用食材名称",
    )
    value: str = Field(
        max_length=80, description="规范化偏好值，如无糖、去冰；忘记整个主体时可为空"
    )
    polarity: Literal["like", "dislike"] = "like"


class MemoryOperation(Preference):
    action: Literal["remember", "forget"]


class MemoryDecision(BaseModel):
    operations: list[MemoryOperation] = Field(default_factory=list)
    query: bool = False


class OrderPreferences(BaseModel):
    preferences: list[Preference] = Field(default_factory=list)


class MemoryState(TypedDict, total=False):
    mode: str
    source_id: str
    message: str
    order: dict
    preferences: list[dict]
    reply: str
    error: str | None
