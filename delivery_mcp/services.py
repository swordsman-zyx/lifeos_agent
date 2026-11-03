import json
import uuid
from datetime import datetime, timedelta, timezone

from shared.database import database, now

from .repository import DeliveryRepository
from .schemas import (
    Address,
    AddressesResult,
    Amounts,
    Cart,
    CartItem,
    CartItemInput,
    Category,
    Food,
    MenuResult,
    Order,
    OrderResult,
    Restaurant,
    RestaurantsResult,
    SubmitResult,
)


class DeliveryService:
    def __init__(self, path, scenario="success"):
        self.path, self.scenario = path, scenario
        self.repo = DeliveryRepository(path)

    def addresses(self) -> AddressesResult:
        rows = [Address(**a) for a in self.repo.all("delivery_addresses")]
        return AddressesResult(
            addresses=rows,
            default_address_id=next((a.address_id for a in rows if a.is_default), None),
        )

    def restaurants(
        self, address_id, keyword="", tags=None, sort_by="rating", page=1, page_size=10
    ) -> RestaurantsResult:
        self.repo.get("delivery_addresses", address_id)
        rows = [Restaurant(**r) for r in self.repo.all("delivery_restaurants")]
        # Demo distance and ETA vary by destination; not real geographic routing.
        if address_id == "office":
            rows = [
                r.model_copy(
                    update={
                        "distance_meters": max(300, 2200 - r.distance_meters),
                        "delivery_minutes": [30, 45],
                    }
                )
                for r in rows
            ]
        rows = [
            r
            for r in rows
            if r.business_status == "open"
            and r.delivery_available
            and (not keyword or keyword in r.name or any(keyword in t for t in r.tags))
            and (not tags or set(tags) & set(r.tags))
        ]
        if sort_by not in {"rating", "distance", "delivery_time", "delivery_fee"}:
            raise ValueError("不支持的排序")
        rows.sort(
            key=lambda r: {
                "rating": -r.rating,
                "distance": r.distance_meters,
                "delivery_time": r.delivery_minutes[0],
                "delivery_fee": r.delivery_fee,
            }[sort_by]
        )
        return RestaurantsResult(
            restaurants=rows[(page - 1) * page_size : page * page_size],
            total=len(rows),
            page=page,
            page_size=page_size,
        )

    def menu(
        self, restaurant_id="", category_id="", keyword="", max_price=None, page=1, page_size=10
    ) -> MenuResult:
        if restaurant_id:
            self.repo.get("delivery_restaurants", restaurant_id)
        foods = [Food(**f) for f in self.repo.all("delivery_foods")]
        foods = [
            f
            for f in foods
            if f.availability == "available"
            and (not restaurant_id or f.restaurant_id == restaurant_id)
            and (not category_id or category_id in f.category_ids)
            and (
                not keyword
                or keyword in f.name
                or keyword in f.description
                or any(keyword in t for t in f.tags)
            )
            and (max_price is None or f.base_price <= max_price)
        ]
        with database(self.path) as db:
            rows = db.execute(
                "SELECT payload FROM delivery_categories WHERE restaurant_id=? ORDER BY rowid",
                (restaurant_id or "mc001",),
            ).fetchall()
        return MenuResult(
            categories=[Category(**json.loads(r["payload"])) for r in rows],
            foods=foods[(page - 1) * page_size : page * page_size],
            total=len(foods),
            page=page,
            page_size=page_size,
        )

    def details(self, food_id) -> Food:
        return Food(**self.repo.get("delivery_foods", food_id))

    def _options(self, groups, selected):
        names, price, consumed = [], 0, set()
        for group in groups:
            choices = [o for o in group.options if o.option_id in selected]
            if not (group.min_select <= len(choices) <= group.max_select):
                raise ValueError(f"请完成选项：{group.name}")
            for option in choices:
                consumed.add(option.option_id)
                names.append(option.name)
                price += option.price_delta
                child_names, child_price, child_ids = self._options(option.option_groups, selected)
                names += child_names
                price += child_price
                consumed |= child_ids
        return names, price, consumed

    def set_cart(self, restaurant_id, address_id, items, cart_id=None, remark="") -> Cart:
        restaurant = next(
            (
                r
                for r in self.restaurants(address_id).restaurants
                if r.restaurant_id == restaurant_id
            ),
            None,
        )
        if not restaurant:
            raise ValueError("餐厅不存在")
        address = Address(**self.repo.get("delivery_addresses", address_id))
        if not items:
            raise ValueError("购物车不能为空")
        lines = []
        for raw in items:
            item = CartItemInput.model_validate(raw)
            food = self.details(item.food_id)
            if food.restaurant_id != restaurant_id:
                raise ValueError("一份订单只能包含一家餐厅的餐品")
            selected = set(item.selected_option_ids)
            names, price, consumed = self._options(food.option_groups, selected)
            if selected != consumed or len(selected) != len(item.selected_option_ids):
                raise ValueError("包含无效或重复规格")
            lines.append(
                CartItem(
                    **item.model_dump(),
                    name=food.name,
                    selected_option_names=names,
                    unit_price=food.base_price + price,
                    packaging_fee=food.packaging_fee,
                )
            )
        subtotal = sum(i.unit_price * i.quantity for i in lines)
        packaging = sum(i.packaging_fee * i.quantity for i in lines)
        cart_id = cart_id or uuid.uuid4().hex
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT version FROM delivery_carts WHERE id=?", (cart_id,)
            ).fetchone()
            version = previous["version"] + 1 if previous else 1
            at = datetime.now(timezone.utc)
            cart = Cart(
                cart_id=cart_id,
                cart_version=version,
                restaurant=restaurant,
                delivery_address=address,
                items=lines,
                amounts=Amounts(
                    subtotal=subtotal,
                    packaging_fee=packaging,
                    delivery_fee=restaurant.delivery_fee,
                    total=subtotal + packaging + restaurant.delivery_fee,
                ),
                estimated_arrival=[
                    (at + timedelta(minutes=m)).isoformat() for m in restaurant.delivery_minutes
                ],
                remark=remark,
            )
            db.execute(
                "INSERT INTO delivery_carts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload",
                (cart_id, version, cart.model_dump_json()),
            )
        return cart

    def submit(self, cart_id, cart_version, idempotency_key) -> SubmitResult:
        if not idempotency_key.strip():
            raise ValueError("幂等键不能为空")
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM delivery_orders WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if previous:
                if previous["cart_id"] != cart_id or previous["cart_version"] != cart_version:
                    raise ValueError("幂等键已经用于其他购物车")
                return self._submit_result(previous)
            duplicate = db.execute(
                "SELECT * FROM delivery_orders WHERE cart_id=? AND cart_version=?",
                (cart_id, cart_version),
            ).fetchone()
            if duplicate:
                raise ValueError("该购物车版本已提交，请查询原订单")
            row = db.execute("SELECT * FROM delivery_carts WHERE id=?", (cart_id,)).fetchone()
            if not row or row["version"] != cart_version:
                raise ValueError("购物车版本不匹配")
            cart = Cart.model_validate_json(row["payload"])
            scenario = self.scenario
            outcome = (
                "rejected"
                if scenario in {"rejected", "payment_failed"}
                else "pending"
                if scenario in {"pending", "unknown"}
                else "succeeded"
            )
            order = Order(
                order_id="DEMO-" + uuid.uuid4().hex[:12].upper(),
                idempotency_key=idempotency_key,
                cart=cart,
                order_status="cancelled"
                if outcome == "rejected"
                else "processing"
                if outcome == "pending"
                else "confirmed",
                payment_status="failed"
                if outcome == "rejected"
                else "processing"
                if outcome == "pending"
                else "paid",
                created_at=now(),
            )
            message = "模拟支付失败，请重新点餐。" if outcome == "rejected" else ""
            db.execute(
                "INSERT INTO delivery_orders VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    order.order_id,
                    idempotency_key,
                    cart_id,
                    cart_version,
                    outcome,
                    order.model_dump_json(),
                    message,
                    scenario,
                    order.created_at,
                ),
            )
        if scenario in {"lost_response", "unknown"}:
            return SubmitResult(outcome="unknown", message="模拟提交响应丢失，结果需查询。")
        return SubmitResult(
            outcome=outcome,
            order=order,
            message=message,
            error_code="PAYMENT_FAILED" if outcome == "rejected" else None,
        )

    def _submit_result(self, row):
        return SubmitResult(
            outcome=row["outcome"],
            order=Order.model_validate_json(row["payload"]),
            message=row["message"],
            error_code="PAYMENT_FAILED" if row["outcome"] == "rejected" else None,
        )

    def get_order(self, order_id=None, idempotency_key=None) -> OrderResult:
        if not order_id and not idempotency_key:
            raise ValueError("请提供订单 ID 或幂等键")
        with database(self.path) as db:
            row = db.execute(
                "SELECT * FROM delivery_orders WHERE "
                + ("id=?" if order_id else "idempotency_key=?"),
                (order_id or idempotency_key,),
            ).fetchone()
            if not row:
                return OrderResult(
                    found=False, outcome="unknown", message="暂未查到订单，不代表提交失败。"
                )
            if order_id and idempotency_key and row["idempotency_key"] != idempotency_key:
                raise ValueError("订单 ID 与幂等键不匹配")
            result = self._submit_result(row)
            if (
                row["scenario"] == "pending"
                and (
                    datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
                ).total_seconds()
                >= 2
            ):
                result.order.order_status = "confirmed"
                result.order.payment_status = "paid"
                result.outcome = "succeeded"
                db.execute(
                    "UPDATE delivery_orders SET outcome=?,payload=? WHERE id=?",
                    ("succeeded", result.order.model_dump_json(), row["id"]),
                )
            if row["scenario"] == "unknown":
                return OrderResult(
                    found=False, outcome="unknown", message="模拟查询暂时不可用，提交结果尚未确认。"
                )
        return OrderResult(
            found=True, outcome=result.outcome, order=result.order, message=result.message
        )
