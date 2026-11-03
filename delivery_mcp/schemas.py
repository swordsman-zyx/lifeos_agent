from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Address(Model):
    address_id: str
    contact: str
    phone: str = Field(description="脱敏电话")
    address: str
    is_default: bool = False


class Restaurant(Model):
    restaurant_id: str
    name: str
    address: str
    distance_meters: int
    delivery_minutes: list[int] = Field(description="预计配送分钟数：[最短，最长]")
    delivery_fee: int = Field(description="配送费，人民币分")
    minimum_order_amount: int = Field(description="起送金额，人民币分；演示版不阻止结算")
    tags: list[str]
    rating: float = Field(ge=0, le=5)
    rating_count: int
    business_status: str = "open"
    delivery_available: bool = True


class Option(Model):
    option_id: str
    name: str
    price_delta: int = Field(default=0, description="加价，人民币分")
    option_groups: list["OptionGroup"] = Field(
        default_factory=list, description="选中该组件后才生效的子选项组"
    )


class OptionGroup(Model):
    group_id: str
    name: str
    required: bool = True
    min_select: int = 1
    max_select: int = 1
    options: list[Option]


class Category(Model):
    category_id: str
    name: str
    sort_order: int = 0


class Food(Model):
    food_id: str
    restaurant_id: str
    name: str
    description: str
    image_url: str | None = None
    category_ids: list[str]
    tags: list[str]
    base_price: int = Field(description="基础价格，人民币分")
    original_price: int | None = None
    portion_description: str
    packaging_fee: int = Field(default=0, description="每份包装费，人民币分")
    availability: str = "available"
    option_groups: list[OptionGroup] = Field(default_factory=list)


class CartItemInput(Model):
    food_id: str
    quantity: int = Field(default=1, ge=1, le=20)
    selected_option_ids: list[str] = Field(
        default_factory=list, description="已选规格及套餐子规格 ID，无规格餐品传空列表"
    )


class CartItem(CartItemInput):
    name: str
    selected_option_names: list[str]
    unit_price: int = Field(description="含选项的单价，人民币分")
    packaging_fee: int


class Amounts(Model):
    currency: Literal["CNY"] = "CNY"
    subtotal: int
    packaging_fee: int
    delivery_fee: int
    discount: int = 0
    total: int = Field(description="应付金额，人民币分")


class Cart(Model):
    cart_id: str
    cart_version: int
    restaurant: Restaurant
    delivery_address: Address
    items: list[CartItem]
    amounts: Amounts
    estimated_arrival: list[str] = Field(description="预计送达时间范围，带时区 ISO 8601")
    remark: str = ""


class Order(Model):
    order_id: str
    idempotency_key: str
    cart: Cart
    order_status: Literal["processing", "confirmed", "cancelled"]
    payment_status: Literal["unpaid", "processing", "paid", "failed"]
    created_at: str


class AddressesResult(Model):
    addresses: list[Address]
    default_address_id: str | None


class RestaurantsResult(Model):
    restaurants: list[Restaurant]
    total: int
    page: int
    page_size: int


class MenuResult(Model):
    categories: list[Category]
    foods: list[Food]
    total: int
    page: int
    page_size: int


class SubmitResult(Model):
    outcome: Literal["succeeded", "rejected", "pending", "unknown"]
    order: Order | None = None
    error_code: str | None = None
    message: str = ""


class OrderResult(Model):
    found: bool
    outcome: Literal["succeeded", "rejected", "pending", "unknown"]
    order: Order | None = None
    message: str = ""
