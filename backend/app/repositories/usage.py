import json

from shared.database import database, now


class UsageRepository:
    """Append-only model call ledger, independent of graph checkpoints."""

    def __init__(self, path):
        self.path = path
        with database(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS usage_meta (started_at TEXT NOT NULL);
                INSERT INTO usage_meta SELECT CURRENT_TIMESTAMP
                    WHERE NOT EXISTS (SELECT 1 FROM usage_meta);
                CREATE TABLE IF NOT EXISTS usage_operations (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    message_id TEXT, summary TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_calls (
                    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, agent TEXT NOT NULL, model TEXT,
                    status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                    total_tokens INTEGER, created_at TEXT NOT NULL);
            """)
            db.execute("UPDATE usage_calls SET status='unknown' WHERE status='running'")

    def begin(self, operation_id, session_id):
        with database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO usage_operations(id,session_id,created_at) VALUES(?,?,?)",
                (operation_id, session_id, now()),
            )

    def start_call(self, call_id, operation_id, session_id, agent, model):
        self.begin(operation_id, session_id)
        with database(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO usage_calls VALUES(?,?,?,?,?,'running',NULL,NULL,NULL,?)",
                (call_id, operation_id, session_id, agent, model, now()),
            )

    def finish_call(self, call_id, usage=None):
        values = None
        if usage:
            values = (usage.get("input_tokens"), usage.get("output_tokens"))
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
                values = None
        with database(self.path) as db:
            if values is None:
                db.execute(
                    "UPDATE usage_calls SET status='unknown' WHERE id=? AND status='running'",
                    (call_id,),
                )
            else:
                db.execute(
                    """UPDATE usage_calls SET status='known',input_tokens=?,
                    output_tokens=?,total_tokens=? WHERE id=? AND status='running'""",
                    (*values, sum(values), call_id),
                )

    def summary(self, *, session_id=None, operation_id=None):
        where, params = "", ()
        if operation_id is not None:
            where, params = " WHERE operation_id=?", (operation_id,)
        elif session_id is not None:
            where, params = " WHERE session_id=?", (session_id,)
        with database(self.path) as db:
            rows = db.execute("SELECT * FROM usage_calls" + where, params).fetchall()
        result = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "unknown_calls": 0,
            "calls": len(rows),
            "agents": {},
        }
        for row in rows:
            agent = result["agents"].setdefault(
                row["agent"], {"total_tokens": 0, "unknown_calls": 0}
            )
            if row["status"] != "known":
                result["unknown_calls"] += 1
                agent["unknown_calls"] += 1
            else:
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    result[key] += row[key]
                agent["total_tokens"] += row["total_tokens"]
        return result

    def account(self):
        result = self.summary()
        with database(self.path) as db:
            started = db.execute("SELECT started_at FROM usage_meta").fetchone()[0]
        return {
            **result,
            "started_at": started,
        }

    def finish_operation(self, operation_id, message_id=None):
        summary = self.summary(operation_id=operation_id)
        with database(self.path) as db:
            row = db.execute(
                "SELECT session_id,summary FROM usage_operations WHERE id=?", (operation_id,)
            ).fetchone()
        if row is None:
            return
        previous = json.loads(row["summary"]) if row["summary"] else None
        if previous and all(previous[key] == summary[key] for key in summary):
            # A repeated HTTP request must not change the historical session total.
            with database(self.path) as db:
                db.execute(
                    "UPDATE usage_operations SET message_id=COALESCE(message_id,?) WHERE id=?",
                    (message_id, operation_id),
                )
            return
        summary["session_total"] = self.summary(session_id=row["session_id"])["total_tokens"]
        with database(self.path) as db:
            db.execute(
                "UPDATE usage_operations SET message_id=COALESCE(message_id,?), "
                "summary=? WHERE id=?",
                (message_id, json.dumps(summary), operation_id),
            )

    def decorate(self, view):
        sid = view["session_id"]
        with database(self.path) as db:
            rows = db.execute(
                "SELECT message_id,summary FROM usage_operations "
                "WHERE session_id=? AND message_id IS NOT NULL",
                (sid,),
            ).fetchall()
        by_message = {
            row["message_id"]: json.loads(row["summary"]) for row in rows if row["summary"]
        }
        for message in view["messages"]:
            if message["id"] in by_message:
                message["usage"] = by_message[message["id"]]
        view["usage"] = {"account": self.account(), "session": self.summary(session_id=sid)}
        return view
