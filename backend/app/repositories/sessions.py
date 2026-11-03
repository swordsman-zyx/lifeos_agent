import json
import uuid

from shared.database import database, now


class SessionRepository:
    def __init__(self, path):
        self.path = path
        with database(path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, created_at);
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                cart_id TEXT NOT NULL, cart_version INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', idempotency_key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT NOT NULL, session_id TEXT NOT NULL REFERENCES sessions(id),
                payload TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, id)
            );
            """)

    def create(self):
        ident, at = uuid.uuid4().hex, now()
        with database(self.path) as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (ident, "新会话", at, at))
        return self.get(ident)

    def get(self, ident):
        with database(self.path) as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (ident,)).fetchone()
        if not row:
            raise KeyError("会话不存在")
        return dict(row)

    def list(self):
        with database(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY updated_at DESC")]

    def messages(self, ident):
        with database(self.path) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY rowid", (ident,)
                )
            ]

    def add_message(self, session_id, message_id, role, content):
        with database(self.path) as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?)",
                (message_id, session_id, role, content, now()),
            )
            if inserted.rowcount:
                db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))
            if inserted.rowcount and role == "user":
                db.execute(
                    "UPDATE sessions SET title=? WHERE id=? AND title='新会话'",
                    (content[:24], session_id),
                )

    def request(self, sid, rid, payload):
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with database(self.path) as db:
            row = db.execute(
                "SELECT * FROM requests WHERE session_id=? AND id=?", (sid, rid)
            ).fetchone()
            if row and row["payload"] != serialized:
                raise ValueError("消息 ID 已用于其他请求")
            if not row:
                db.execute("INSERT INTO requests VALUES (?,?,?,0)", (rid, sid, serialized))
        return dict(row) if row else None

    def complete(self, sid, rid):
        with database(self.path) as db:
            db.execute("UPDATE requests SET completed=1 WHERE session_id=? AND id=?", (sid, rid))

    def approval(self, ident):
        with database(self.path) as db:
            row = db.execute("SELECT * FROM approvals WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def create_approval(self, sid, cart):
        # Stable ID also makes node replay idempotent after a checkpoint interruption.
        ident = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"{sid}:{cart['cart_id']}:{cart['cart_version']}")
        )
        with database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO approvals VALUES (?,?,?,?,?,?,?)",
                (
                    ident,
                    sid,
                    cart["cart_id"],
                    cart["cart_version"],
                    "pending",
                    uuid.uuid4().hex,
                    now(),
                ),
            )
        return self.approval(ident)

    def decide(self, sid, ident, action):
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM approvals WHERE id=? AND session_id=?", (ident, sid)
            ).fetchone()
            if not row:
                raise ValueError("确认事项不属于当前会话")
            desired = "confirmed" if action == "confirm" else "cancelled"
            if row["status"] not in {"pending", desired}:
                raise ValueError("确认事项已处理，不能变更决定")
            db.execute("UPDATE approvals SET status=? WHERE id=?", (desired, ident))
        return self.approval(ident)
