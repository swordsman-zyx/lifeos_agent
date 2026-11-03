"""SQLite dietary memory. Explicit statements and order evidence have separate lifecycles."""

import json
from collections import defaultdict

from shared.database import database, now

USER_ID = "local-user"


class MemoryRepository:
    def __init__(self, path):
        self.path = path
        with database(path) as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}', error TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dietary_memories (
                user_id TEXT NOT NULL, dimension TEXT NOT NULL, subject TEXT NOT NULL,
                value TEXT NOT NULL, polarity TEXT NOT NULL, active INTEGER NOT NULL,
                source_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,dimension,subject,value,polarity)
            );
            CREATE TABLE IF NOT EXISTS memory_blocks (
                user_id TEXT NOT NULL, dimension TEXT NOT NULL, subject TEXT NOT NULL,
                value TEXT NOT NULL, active INTEGER NOT NULL,
                PRIMARY KEY(user_id,dimension,subject,value)
            );
            CREATE TABLE IF NOT EXISTS order_preferences (
                order_id TEXT NOT NULL, dimension TEXT NOT NULL, subject TEXT NOT NULL,
                value TEXT NOT NULL, polarity TEXT NOT NULL,
                PRIMARY KEY(order_id,dimension,subject,value,polarity)
            );
            """)

    def event(self, ident):
        with database(self.path) as db:
            row = db.execute("SELECT * FROM memory_events WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def fail(self, ident, kind, error):
        with database(self.path) as db:
            db.execute(
                "INSERT INTO memory_events VALUES (?,?, 'failed','{}',?,?) ON CONFLICT(id) DO UPDATE SET status='failed',error=excluded.error,updated_at=excluded.updated_at",
                (ident, kind, error, now()),
            )

    def apply_explicit(self, source_id, operations):
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT status FROM memory_events WHERE id=?", (source_id,)
            ).fetchone()
            if previous and previous["status"] == "done":
                return
            for op in operations:
                dimension, subject, value = op["dimension"], op["subject"], op["value"]
                if op["action"] == "forget":
                    db.execute(
                        "UPDATE dietary_memories SET active=0,updated_at=? WHERE user_id=? AND dimension=? AND subject=? AND (?='' OR value=?)",
                        (now(), USER_ID, dimension, subject, value, value),
                    )
                    db.execute(
                        "INSERT INTO memory_blocks VALUES (?,?,?,?,1) ON CONFLICT(user_id,dimension,subject,value) DO UPDATE SET active=1",
                        (USER_ID, dimension, subject, value),
                    )
                else:
                    # A new explicit statement supersedes the same dietary dimension/subject.
                    db.execute(
                        "UPDATE dietary_memories SET active=0 WHERE user_id=? AND dimension=? AND subject=?",
                        (USER_ID, dimension, subject),
                    )
                    db.execute(
                        "UPDATE memory_blocks SET active=0 WHERE user_id=? AND dimension=? AND subject=? AND value IN (?,'')",
                        (USER_ID, dimension, subject, value),
                    )
                    db.execute(
                        "INSERT INTO dietary_memories VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(user_id,dimension,subject,value,polarity) DO UPDATE SET active=1,source_id=excluded.source_id,updated_at=excluded.updated_at",
                        (USER_ID, dimension, subject, value, op["polarity"], source_id, now()),
                    )
            db.execute(
                "INSERT INTO memory_events VALUES (?, 'message','done',?,NULL,?) ON CONFLICT(id) DO UPDATE SET status='done',payload=excluded.payload,error=NULL,updated_at=excluded.updated_at",
                (source_id, json.dumps(operations, ensure_ascii=False), now()),
            )

    def record_order(self, order_id, preferences):
        ident = "order:" + order_id
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM memory_events WHERE id=? AND status='done'", (ident,)
            ).fetchone():
                return
            for p in preferences:
                # Orders prove positive selections, never ingredient avoidance or health facts.
                if p["polarity"] != "like":
                    continue
                db.execute(
                    "INSERT OR IGNORE INTO order_preferences VALUES (?,?,?,?,?)",
                    (order_id, p["dimension"], p["subject"], p["value"], p["polarity"]),
                )
            db.execute(
                "INSERT INTO memory_events VALUES (?, 'order','done',?,NULL,?) ON CONFLICT(id) DO UPDATE SET status='done',payload=excluded.payload,error=NULL,updated_at=excluded.updated_at",
                (ident, json.dumps(preferences, ensure_ascii=False), now()),
            )

    def preferences(self):
        with database(self.path) as db:
            explicit = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM dietary_memories WHERE user_id=? AND active=1 ORDER BY updated_at DESC",
                    (USER_ID,),
                )
            ]
            blocks = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM memory_blocks WHERE user_id=? AND active=1", (USER_ID,)
                )
            ]
            evidence = db.execute("""SELECT p.*,o.created_at FROM order_preferences p
                JOIN lifeos_orders o ON p.order_id=o.id
                WHERE p.order_id IN (SELECT id FROM lifeos_orders ORDER BY created_at DESC,id DESC LIMIT 10)
                ORDER BY o.created_at DESC,o.id DESC""").fetchall()

        def blocked(dimension, subject, value):
            return any(
                b["dimension"] == dimension
                and b["subject"] == subject
                and b["value"] in {"", value}
                for b in blocks
            )

        winners = {}
        for row in explicit:
            key = (row["dimension"], row["subject"])
            if key not in winners and not blocked(*key, row["value"]):
                winners[key] = {
                    k: row[k] for k in ("dimension", "subject", "value", "polarity", "updated_at")
                }
                winners[key].update(
                    source="explicit", source_ids=[row["source_id"]], evidence_count=1
                )
        grouped = defaultdict(list)
        for row in evidence:
            grouped[(row["dimension"], row["subject"], row["value"], row["polarity"])].append(
                dict(row)
            )
        ranked = sorted(
            grouped.items(),
            key=lambda item: (len(item[1]), max(r["created_at"] for r in item[1]), item[0]),
            reverse=True,
        )
        for (dimension, subject, value, polarity), rows in ranked:
            if (
                len(rows) < 2
                or (dimension, subject) in winners
                or blocked(dimension, subject, value)
            ):
                continue
            winners[(dimension, subject)] = dict(
                dimension=dimension,
                subject=subject,
                value=value,
                polarity=polarity,
                source="inferred",
                source_ids=[r["order_id"] for r in rows],
                evidence_count=len(rows),
                updated_at=max(r["created_at"] for r in rows),
            )
        return list(winners.values())
