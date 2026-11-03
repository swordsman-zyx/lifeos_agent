import json

from shared.database import database, now


class OrderRepository:
    """LifeOS projection of platform-confirmed paid orders, not platform table access."""

    def __init__(self, path):
        self.path = path
        with database(path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS lifeos_orders (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS message_cards (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                message_id TEXT NOT NULL REFERENCES messages(id), kind TEXT NOT NULL,
                payload TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """)

    def save(self, sid, result):
        order = result.get("order")
        if (
            result.get("outcome") != "succeeded"
            or not order
            or order.get("payment_status") != "paid"
        ):
            return
        with database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO lifeos_orders VALUES (?,?,?,?)",
                (
                    order["order_id"],
                    sid,
                    json.dumps(order, ensure_ascii=False),
                    order["created_at"],
                ),
            )

    def recent(self, limit=10):
        with database(self.path) as db:
            return [
                dict(order=json.loads(r["payload"]), session_id=r["session_id"])
                for r in db.execute(
                    "SELECT * FROM lifeos_orders ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)
                )
            ]

    def pending_learning(self, limit=3):
        with database(self.path) as db:
            rows = db.execute(
                "SELECT o.payload FROM lifeos_orders o LEFT JOIN memory_events e ON e.id='order:'||o.id WHERE (e.id IS NULL OR e.status!='done') AND o.id IN (SELECT id FROM lifeos_orders ORDER BY created_at DESC,id DESC LIMIT 10) ORDER BY o.created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def card(self, sid, message_id, approval_id, payload):
        with database(self.path) as db:
            db.execute(
                "INSERT INTO message_cards VALUES (?,?,?,'order',?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                (approval_id, sid, message_id, json.dumps(payload, ensure_ascii=False), now()),
            )

    def card_anchor(self, approval_id):
        with database(self.path) as db:
            row = db.execute(
                "SELECT message_id FROM message_cards WHERE id=?", (approval_id,)
            ).fetchone()
        return row["message_id"] if row else None

    def backfill_candidates(self):
        with database(self.path) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT a.* FROM approvals a WHERE a.status='confirmed' AND NOT EXISTS (SELECT 1 FROM lifeos_orders o WHERE json_extract(o.payload,'$.idempotency_key')=a.idempotency_key) ORDER BY a.created_at DESC LIMIT 10"
                )
            ]

    def attach_cards(self, sid, messages):
        with database(self.path) as db:
            rows = db.execute(
                "SELECT * FROM message_cards WHERE session_id=? ORDER BY rowid", (sid,)
            ).fetchall()
        cards = {}
        for r in rows:
            cards.setdefault(r["message_id"], []).append(json.loads(r["payload"]))
        return [{**m, "cards": cards.get(m["id"], [])} for m in messages]
