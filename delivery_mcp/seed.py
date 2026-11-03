"""Idempotent catalog seed; never replaces existing carts or orders."""

from shared.config import settings
from shared.database import database

from .schemas import Address, Category, Food, Option, OptionGroup, Restaurant


def group(gid, name, choices):
    return OptionGroup(
        group_id=gid, name=name, options=[Option(option_id=i, name=n) for i, n in choices]
    )


def seed(path=None):
    path = path or settings.db_path
    addresses = [
        Address(
            address_id="home",
            contact="林同学",
            phone="138****0001",
            address="上海市黄浦区人民广场演示公寓 101 室",
            is_default=True,
        ),
        Address(
            address_id="office",
            contact="林同学",
            phone="138****0001",
            address="上海市静安区南京西路演示办公楼 201 室",
        ),
    ]
    restaurants = [
        Restaurant(
            restaurant_id="mc001",
            name="麦当劳（人民广场店）",
            address="上海市黄浦区人民广场",
            distance_meters=650,
            delivery_minutes=[25, 40],
            delivery_fee=300,
            minimum_order_amount=2000,
            tags=["汉堡快餐", "便当简餐"],
            rating=4.8,
            rating_count=1280,
        ),
        Restaurant(
            restaurant_id="bk001",
            name="汉堡王（南京东路店）",
            address="上海市黄浦区南京东路",
            distance_meters=1200,
            delivery_minutes=[30, 45],
            delivery_fee=400,
            minimum_order_amount=2000,
            tags=["汉堡快餐"],
            rating=4.7,
            rating_count=830,
        ),
        Restaurant(
            restaurant_id="hf001",
            name="和府捞面（人民广场店）",
            address="上海市黄浦区人民广场",
            distance_meters=900,
            delivery_minutes=[30, 45],
            delivery_fee=300,
            minimum_order_amount=2000,
            tags=["便当简餐", "面食"],
            rating=4.6,
            rating_count=680,
        ),
        Restaurant(
            restaurant_id="tea001",
            name="茶日常（静安店）",
            address="上海市静安区南京西路",
            distance_meters=1600,
            delivery_minutes=[35, 50],
            delivery_fee=200,
            minimum_order_amount=1500,
            tags=["奶茶咖啡"],
            rating=4.8,
            rating_count=560,
        ),
    ]
    temperature = group(
        "temperature", "温度", [("normal_ice", "正常冰"), ("less_ice", "少冰"), ("no_ice", "去冰")]
    )
    combo = [
        group(
            "main",
            "主食",
            [("double_cheese", "双层吉士堡"), ("mc_chicken", "麦香鸡"), ("mc_fish", "麦香鱼")],
        ),
        OptionGroup(
            group_id="side",
            name="饮品或小食",
            options=[
                Option(option_id="coke", name="有糖可乐", option_groups=[temperature]),
                Option(option_id="zero_coke", name="无糖可乐", option_groups=[temperature]),
                Option(option_id="small_fries", name="小薯条"),
            ],
        ),
    ]
    foods = [
        Food(
            food_id="mc_combo",
            restaurant_id="mc001",
            name="麦当劳1+1随心配",
            description="任选一份主食与饮品或小食，双层吉士堡也可选",
            category_ids=["deals", "popular"],
            tags=["套餐", "汉堡", "热销"],
            base_price=1390,
            original_price=2200,
            portion_description="一人份",
            packaging_fee=100,
            option_groups=combo,
        ),
        Food(
            food_id="mc_burger",
            restaurant_id="mc001",
            name="双层吉士堡",
            description="双层牛肉饼搭配芝士",
            category_ids=["popular", "main"],
            tags=["汉堡"],
            base_price=2200,
            portion_description="一个，约 180g",
            packaging_fee=100,
        ),
        Food(
            food_id="bk_burger",
            restaurant_id="bk001",
            name="皇堡",
            description="火烤牛肉汉堡，搭配生菜和番茄",
            category_ids=["popular", "main"],
            tags=["汉堡"],
            base_price=2600,
            portion_description="一个，约 250g",
            packaging_fee=100,
        ),
        Food(
            food_id="hf_noodle",
            restaurant_id="hf001",
            name="番茄猪软骨面",
            description="番茄汤底与猪软骨",
            category_ids=["popular", "main"],
            tags=["面食", "不辣"],
            base_price=3200,
            portion_description="一人份，约 500g",
            packaging_fee=200,
        ),
        Food(
            food_id="tea_milk",
            restaurant_id="tea001",
            name="茉莉鲜奶茶",
            description="茉莉茶汤与鲜牛乳",
            category_ids=["popular", "drinks"],
            tags=["奶茶"],
            base_price=1600,
            portion_description="500ml",
            packaging_fee=0,
            option_groups=[
                temperature,
                group(
                    "sweetness",
                    "甜度",
                    [("full_sugar", "全糖"), ("half_sugar", "半糖"), ("no_sugar", "无糖")],
                ),
            ],
        ),
    ]
    with database(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS delivery_addresses (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_restaurants (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_categories (id TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_foods (id TEXT PRIMARY KEY, restaurant_id TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_carts (id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_orders (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, cart_id TEXT NOT NULL,
            cart_version INTEGER NOT NULL, outcome TEXT NOT NULL, payload TEXT, message TEXT NOT NULL,
            scenario TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(cart_id, cart_version)
        );
        """)
        for table, values in [
            ("delivery_addresses", addresses),
            ("delivery_restaurants", restaurants),
        ]:
            for value in values:
                ident = value.address_id if isinstance(value, Address) else value.restaurant_id
                db.execute(
                    f"INSERT OR IGNORE INTO {table} VALUES (?, ?)", (ident, value.model_dump_json())
                )
        for r in restaurants:
            for i, name in [
                ("deals", "优惠"),
                ("popular", "热销"),
                ("main", "主食"),
                ("drinks", "饮品"),
            ]:
                cat = Category(category_id=i, name=name)
                db.execute(
                    "INSERT OR IGNORE INTO delivery_categories VALUES (?, ?, ?)",
                    (r.restaurant_id + ":" + i, r.restaurant_id, cat.model_dump_json()),
                )
        for f in foods:
            db.execute(
                "INSERT OR IGNORE INTO delivery_foods VALUES (?, ?, ?)",
                (f.food_id, f.restaurant_id, f.model_dump_json()),
            )


if __name__ == "__main__":
    seed()
    print(f"演示数据已初始化：{settings.db_path}")
