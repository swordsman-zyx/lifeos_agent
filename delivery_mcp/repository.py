import json

from shared.database import database

# Only these internal table names may be interpolated into SQL.
TABLES = {"delivery_addresses", "delivery_restaurants", "delivery_foods", "delivery_categories"}


class DeliveryRepository:
    def __init__(self, path):
        self.path = path

    def all(self, table):
        if table not in TABLES:
            raise ValueError("Unsupported table")
        with database(self.path) as db:
            return [
                json.loads(r["payload"])
                for r in db.execute(f"SELECT payload FROM {table} ORDER BY rowid")
            ]

    def get(self, table, ident):
        if table not in TABLES:
            raise ValueError("Unsupported table")
        with database(self.path) as db:
            row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError(f"找不到对象：{ident}")
        return json.loads(row["payload"])
