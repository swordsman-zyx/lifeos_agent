from typing import Literal

from pydantic import BaseModel

from ..food.state import FoodState


class RouteDecision(BaseModel):
    intent: Literal["food", "memory", "memory_food", "unsupported"]
    reply: str = ""


class LifeOSState(FoodState, total=False):
    user_message: str
    intent: str
    food_active: bool
    memory_reply: str
