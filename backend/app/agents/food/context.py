from dataclasses import dataclass
from typing import Any

from ...repositories.sessions import SessionRepository
from .tools import DeliveryMCPClient


@dataclass(frozen=True)
class FoodContext:
    """Runtime dependencies; never serialized into a checkpoint."""

    llm: Any
    client: DeliveryMCPClient
    repository: SessionRepository
    retry_delays: tuple[float, ...] = (1, 2, 4)

    def __post_init__(self):
        if self.llm is None:
            raise ValueError("Food Agent 必须提供模型客户端")
