from dataclasses import dataclass

from ...repositories.memory import MemoryRepository
from ...repositories.orders import OrderRepository
from ..food.context import FoodContext


@dataclass(frozen=True, kw_only=True)
class LifeOSContext(FoodContext):
    memory: MemoryRepository
    orders: OrderRepository
