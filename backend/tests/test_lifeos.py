import unittest
import uuid

from backend.app.agents.lifeos.service import interrupted_payload
from backend.app.main import create_app

from . import test_flow as legacy_tests

EXACT = legacy_tests.EXACT


class LifeOSFlowTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(legacy_tests.FlowTests.setUpClass.__func__)
    tearDownClass = classmethod(legacy_tests.FlowTests.tearDownClass.__func__)
    asyncSetUp = legacy_tests.FlowTests.asyncSetUp
    asyncTearDown = legacy_tests.FlowTests.asyncTearDown
    post = legacy_tests.FlowTests.post
    queue_order = legacy_tests.FlowTests.queue_order
    count_orders = legacy_tests.FlowTests.count_orders
    restart_platform = legacy_tests.FlowTests.restart_platform

    def route(self, intent="food"):
        self.model.enqueue_structured("RouteDecision", {"intent": intent})

    def learn(self, preferences=None):
        self.model.enqueue_structured("OrderPreferences", {"preferences": preferences or []})

    async def chat(self, message=EXACT, selection=None, message_id=None):
        return await self.post(
            "/api/lifeos/chat",
            {
                "session_id": self.sid,
                "client_message_id": message_id or uuid.uuid4().hex,
                "message": message,
                "selection": selection,
            },
        )

    async def decide(self, view, action="confirm"):
        return await self.post(
            "/api/lifeos/resume",
            {"session_id": self.sid, "approval_id": view["approval"]["id"], "action": action},
        )

    async def reopen(self):
        import httpx

        await self.http.aclose()
        await self.life.__aexit__(None, None, None)
        self.app = create_app(
            client=self.gateway, db_path=self.db_path, retry_delays=(), llm=self.model
        )
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def test_subgraph_interrupt_and_top_level_resume(self):
        self.route()
        self.queue_order()
        view = await self.chat()
        self.assertEqual(view["status"], "awaiting_confirmation")
        snapshot = await self.app.state.service.graph.aget_state(
            self.app.state.service.config(self.sid), subgraphs=True
        )
        self.assertIsNotNone(interrupted_payload(snapshot))
        self.assertEqual(len(view["messages"][-1]["cards"]), 1)
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 0)
        await self.reopen()
        restored = (await self.http.get("/api/sessions/" + self.sid)).json()
        self.assertEqual(restored["approval"]["id"], view["approval"]["id"])
        self.learn()
        done = await self.decide(restored)
        self.assertEqual(done["order_result"]["outcome"], "succeeded")
        again = await self.decide(restored)
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)
        self.assertEqual(
            again["order_result"]["order"]["order_id"], done["order_result"]["order"]["order_id"]
        )
        recent = (await self.http.get("/api/orders")).json()["orders"]
        self.assertTrue(
            any(r["order"]["order_id"] == done["order_result"]["order"]["order_id"] for r in recent)
        )

    async def test_memory_then_food_only_recommends(self):
        self.route("memory")
        self.model.enqueue_structured(
            "MemoryDecision",
            {
                "operations": [
                    {
                        "action": "remember",
                        "dimension": "sweetness",
                        "subject": "饮品",
                        "value": "无糖",
                        "polarity": "like",
                    }
                ]
            },
        )
        view = await self.chat("记住，我平时喝无糖")
        self.assertEqual(view["dietary_preferences"][0]["value"], "无糖")
        self.route()
        self.model.enqueue(
            ("get_food_details", {"food_id": "mc_combo"}), "请选择本次套餐的主食、饮品与温度。"
        )
        view = await self.chat("帮我点个1+1套餐")
        self.assertIsNone(view["cart"])
        self.assertIn("无糖", self.model.calls[-1][0].content)
        self.assertIn("不根据记忆擅自变更", self.model.calls[-1][0].content)

    async def test_food_state_survives_memory_detour(self):
        self.route()
        self.model.enqueue(("get_food_details", {"food_id": "mc_combo"}), "请选主食和饮品。")
        await self.chat("麦当劳1+1")
        self.route("memory")
        self.model.enqueue_structured("MemoryDecision", {"query": True})
        await self.chat("你记得我的偏好吗")
        self.route()
        self.model.enqueue(
            (
                "set_cart",
                {
                    "restaurant_id": "mc001",
                    "address_id": "home",
                    "items": [
                        {
                            "food_id": "mc_combo",
                            "selected_option_ids": ["double_cheese", "zero_coke", "no_ice"],
                        }
                    ],
                },
            )
        )
        view = await self.chat("双层吉士堡，无糖可乐去冰")
        self.assertEqual(view["status"], "awaiting_confirmation")
        self.assertTrue(
            any(
                getattr(m, "name", None) == "get_food_details" or getattr(m, "tool_call_id", None)
                for m in self.model.calls[-1]
            )
        )
        cancelled = await self.decide(view, "cancel")
        self.assertEqual(cancelled["order_result"]["outcome"], "cancelled")
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 0)

    async def test_duplicate_message_and_memory_event(self):
        self.route("memory")
        self.model.enqueue_structured(
            "MemoryDecision",
            {
                "operations": [
                    {
                        "action": "remember",
                        "dimension": "flavor",
                        "subject": "口味",
                        "value": "清淡",
                        "polarity": "like",
                    }
                ]
            },
        )
        rid = uuid.uuid4().hex
        first = await self.chat("我喜欢清淡", message_id=rid)
        second = await self.chat("我喜欢清淡", message_id=rid)
        self.assertEqual(len(first["messages"]), len(second["messages"]))
        self.assertEqual(len(second["dietary_preferences"]), 1)

    async def test_order_success_survives_learning_failure(self):
        self.route()
        self.queue_order()
        view = await self.chat()
        # No extraction response: failure must not obscure the successful order.
        with self.assertLogs("backend.app.agents.memory.nodes", level="ERROR"):
            done = await self.decide(view)
        self.assertEqual(done["order_result"]["outcome"], "succeeded")
        order = done["order_result"]["order"]
        self.assertEqual(
            self.app.state.service.context.memory.event("order:" + order["order_id"])["status"],
            "failed",
        )
        self.learn()
        self.route()
        self.model.enqueue("你想吃什么？")
        await self.chat("继续帮我选餐")
        self.assertEqual(
            self.app.state.service.context.memory.event("order:" + order["order_id"])["status"],
            "done",
        )

    async def test_consecutive_orders_have_separate_persisted_cards(self):
        self.route()
        self.queue_order()
        first = await self.chat()
        self.learn()
        await self.decide(first)
        self.route()
        self.model.enqueue("请告诉我第二餐想吃什么。")
        question = await self.chat("再点一餐")
        self.assertIsNone(question["order_result"])
        self.assertIsNone(question["cart"])
        self.route()
        self.queue_order()
        second = await self.chat()
        self.assertNotEqual(first["approval"]["id"], second["approval"]["id"])
        cancelled = await self.decide(second, "cancel")
        cards = [c for m in cancelled["messages"] for c in m.get("cards", [])]
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["order_result"]["outcome"], "succeeded")
        self.assertEqual(cards[1]["order_result"]["outcome"], "cancelled")

    async def test_memory_food_combination_and_forget(self):
        self.route("memory_food")
        self.model.enqueue_structured(
            "MemoryDecision",
            {
                "operations": [
                    {
                        "action": "remember",
                        "dimension": "flavor",
                        "subject": "口味",
                        "value": "清淡",
                        "polarity": "like",
                    }
                ]
            },
        )
        food = (await self.gateway.call("get_menu", {"keyword": "面"}))["foods"][0]
        self.model.enqueue(
            ("get_menu", {"keyword": "面"}),
            ("PresentCandidates", {
                "kind": "food",
                "items": [{"id": food["food_id"], "reason": "可以看看这款面"}],
            }),
        )
        view = await self.chat("我平时喜欢清淡，帮我选一碗面")
        self.assertEqual(view["dietary_preferences"][0]["value"], "清淡")
        self.assertTrue(view["candidates"])
        self.route("memory")
        self.model.enqueue_structured(
            "MemoryDecision",
            {
                "operations": [
                    {
                        "action": "forget",
                        "dimension": "flavor",
                        "subject": "口味",
                        "value": "清淡",
                        "polarity": "like",
                    }
                ]
            },
        )
        forgotten = await self.chat("忘掉我喜欢清淡")
        self.assertEqual(forgotten["dietary_preferences"], [])

    async def test_legacy_pending_order_can_resume_from_lifeos(self):
        self.queue_order()
        view = await legacy_tests.FlowTests.chat(self, EXACT)
        self.assertEqual(view["status"], "awaiting_confirmation")
        restored = (await self.http.get("/api/sessions/" + self.sid)).json()
        self.assertTrue(restored["legacy"])
        completed = await self.decide(restored, "cancel")
        self.assertEqual(completed["order_result"]["outcome"], "cancelled")
        self.route("memory")
        self.model.enqueue_structured("MemoryDecision", {"query": True})
        new_view = await self.chat("你记得我的偏好吗")
        self.assertNotIn("legacy", new_view)

    async def test_committed_order_recovers_through_parent(self):
        self.route()
        self.queue_order()
        view = await self.chat()
        record = self.app.state.repository.decide(self.sid, view["approval"]["id"], "confirm")
        existing = await self.gateway.call(
            "submit_order",
            {
                "cart_id": view["cart"]["cart_id"],
                "cart_version": view["cart"]["cart_version"],
                "idempotency_key": record["idempotency_key"],
            },
        )
        self.learn()
        await self.reopen()
        restored = (await self.http.get("/api/sessions/" + self.sid)).json()
        self.assertEqual(
            restored["order_result"]["order"]["order_id"], existing["order"]["order_id"]
        )
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)

    async def test_unknown_order_refresh_keeps_original_preference_snapshot(self):
        try:
            await self.restart_platform("unknown")
            self.route()
            self.queue_order()
            view = await self.chat()
            original = view.get("dietary_preferences", [])
            pending = await self.decide(view)
            self.assertEqual(pending["status"], "order_pending")
            self.app.state.service.context.memory.apply_explicit(
                "another-session-change",
                [
                    {
                        "action": "remember",
                        "dimension": "flavor",
                        "subject": "口味",
                        "value": "微辣",
                        "polarity": "like",
                    }
                ],
            )
            refreshed = await self.post("/api/lifeos/sessions/" + self.sid + "/order/refresh", {})
            self.assertEqual(refreshed["status"], "order_pending")
            self.assertEqual(refreshed["dietary_preferences"], original)
            self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)
        finally:
            await self.restart_platform("success")
