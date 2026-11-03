"""Run: python -m delivery_mcp.server"""

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.config import settings

from .schemas import (
    AddressesResult,
    Cart,
    CartItemInput,
    Food,
    MenuResult,
    OrderResult,
    RestaurantsResult,
    SubmitResult,
)
from .seed import seed
from .services import DeliveryService

seed()
service = DeliveryService(settings.db_path, settings.delivery_scenario)
mcp = FastMCP(
    "LifeOS Demo Delivery",
    host="127.0.0.1",
    port=settings.mcp_port,
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def list_delivery_addresses() -> AddressesResult:
    """获取演示用户的配送地址。默认地址可以直接使用，用户可改选其他地址。"""
    return service.addresses()


@mcp.tool()
def search_restaurants(
    address_id: Annotated[str, Field(description="配送地址 ID")],
    keyword: str = "",
    tags: list[str] | None = None,
    sort_by: Literal["rating", "distance", "delivery_time", "delivery_fee"] = "rating",
    page: Annotated[int, Field(ge=1)] = 1,
    page_size: Annotated[int, Field(ge=1, le=50)] = 10,
) -> RestaurantsResult:
    """搜索可配送且营业中的餐厅，按评分、距离、配送时间或配送费排序。金额均为人民币分。"""
    return service.restaurants(address_id, keyword, tags, sort_by, page, page_size)


@mcp.tool()
def get_menu(
    restaurant_id: str = "",
    category_id: str = "",
    keyword: str = "",
    max_price: Annotated[int | None, Field(description="单品基础价格上限，人民币分", ge=0)] = None,
    page: Annotated[int, Field(ge=1)] = 1,
    page_size: Annotated[int, Field(ge=1, le=50)] = 10,
) -> MenuResult:
    """浏览或搜索餐品。restaurant_id 留空时跨餐厅搜索，支持分类、关键词、预算和分页。金额均为人民币分。"""
    return service.menu(restaurant_id, category_id, keyword, max_price, page, page_size)


@mcp.tool()
def get_food_details(food_id: str) -> Food:
    """查询完整餐品信息和合法规格。选中套餐组件后，需要选择该组件的必填子规格。金额均为人民币分。"""
    return service.details(food_id)


@mcp.tool()
def set_cart(
    restaurant_id: str,
    address_id: str,
    items: Annotated[list[CartItemInput], Field(min_length=1, max_length=20)],
    cart_id: str | None = None,
    remark: str = "",
) -> Cart:
    """以完整餐品列表设置购物车，返回地址、餐品、费用和送达时间，金额均为人民币分。仅完成所有必选规格后调用。不会下单或支付；无独立报价步骤。"""
    return service.set_cart(restaurant_id, address_id, items, cart_id, remark)


@mcp.tool()
def submit_order(cart_id: str, cart_version: int, idempotency_key: str) -> SubmitResult:
    """提交指定购物车并模拟支付。仅人工确认后由工作流调用。同一次提交必须复用幂等键。"""
    return service.submit(cart_id, cart_version, idempotency_key)


@mcp.tool()
def get_order(order_id: str | None = None, idempotency_key: str | None = None) -> OrderResult:
    """查询订单和模拟支付状态，至少提供一种 ID。暂未找到不代表提交失败。"""
    return service.get_order(order_id, idempotency_key)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
