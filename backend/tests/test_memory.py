import tempfile
import unittest
from pathlib import Path

from backend.app.repositories.memory import MemoryRepository
from backend.app.repositories.orders import OrderRepository
from backend.app.repositories.sessions import SessionRepository
from shared.config import ROOT


class MemoryTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / ".test-output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.path = Path(self.temp.name) / "memory.sqlite3"
        sessions = SessionRepository(self.path)
        self.sid = sessions.create()["id"]
        self.orders = OrderRepository(self.path)
        self.memory = MemoryRepository(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def preference(self, value="无糖", **updates):
        return {
            "dimension": "sweetness",
            "subject": "饮品",
            "value": value,
            "polarity": "like",
            **updates,
        }

    def order(self, number, value="无糖", preferences=None):
        order_id = "order-" + str(number)
        self.orders.save(
            self.sid,
            {
                "outcome": "succeeded",
                "order": {
                    "order_id": order_id,
                    "payment_status": "paid",
                    "created_at": f"2026-11-{number:02d}T12:00:00+08:00",
                },
            },
        )
        self.memory.record_order(order_id, preferences or [self.preference(value)])

    def explicit(self, source, value="无糖", action="remember"):
        self.memory.apply_explicit(source, [dict(action=action, **self.preference(value))])

    def test_two_distinct_orders_and_replay_dedup(self):
        self.order(1)
        self.order(1)
        self.assertEqual(self.memory.preferences(), [])
        self.order(2)
        result = self.memory.preferences()[0]
        self.assertEqual(result["evidence_count"], 2)
        self.assertEqual(result["source"], "inferred")

    def test_explicit_overrides_and_latest_correction(self):
        self.order(1)
        self.order(2)
        self.explicit("m1", "全糖")
        self.assertEqual(self.memory.preferences()[0]["value"], "全糖")
        self.assertEqual(self.memory.preferences()[0]["source"], "explicit")
        self.explicit("m2", "半糖")
        self.assertEqual(self.memory.preferences()[0]["value"], "半糖")
        self.explicit("m1", "全糖")
        self.assertEqual(self.memory.preferences()[0]["value"], "半糖")

    def test_forget_blocks_old_and_new_inference_until_explicit_remember(self):
        self.order(1)
        self.order(2)
        self.explicit("forget", action="forget")
        self.assertEqual(self.memory.preferences(), [])
        self.order(3)
        self.assertEqual(self.memory.preferences(), [])
        self.explicit("remember")
        self.assertEqual(self.memory.preferences()[0]["source"], "explicit")

    def test_forget_entire_dimension_subject(self):
        self.explicit("m1")
        self.explicit("forget-all", value="", action="forget")
        self.order(1, "全糖")
        self.order(2, "全糖")
        self.assertEqual(self.memory.preferences(), [])

    def test_recent_ten_and_ties_use_latest(self):
        self.order(1)
        self.order(2)
        for number in range(3, 13):
            self.order(number, "半糖")
        self.assertEqual(self.memory.preferences()[0]["value"], "半糖")
        self.assertEqual(self.memory.preferences()[0]["evidence_count"], 10)
        self.explicit("block-half", value="半糖", action="forget")
        self.assertEqual(self.memory.preferences(), [])

    def test_tie_uses_latest_and_ignores_negative_order_inference(self):
        self.order(1)
        self.order(2)
        self.order(3, "半糖")
        self.order(4, "半糖")
        self.assertEqual(self.memory.preferences()[0]["value"], "半糖")
        for number in [5, 6]:
            self.order(
                number,
                preferences=[
                    self.preference("辣", dimension="flavor", subject="辣度", polarity="dislike")
                ],
            )
        self.assertFalse(any(p["dimension"] == "flavor" for p in self.memory.preferences()))

    def test_failed_cancelled_and_unpaid_orders_not_indexed(self):
        for outcome, payment in [
            ("rejected", "failed"),
            ("pending", "processing"),
            ("succeeded", "unpaid"),
            ("cancelled", "unpaid"),
        ]:
            self.orders.save(
                self.sid,
                {
                    "outcome": outcome,
                    "order": {
                        "order_id": outcome + payment,
                        "payment_status": payment,
                        "created_at": "2026-11-01",
                    },
                },
            )
        self.assertEqual(self.orders.recent(), [])
