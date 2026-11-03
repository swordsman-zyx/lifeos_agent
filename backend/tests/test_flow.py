"""Integration tests use a real MCP HTTP process and the same SQLite file as the API."""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.messages import ToolMessage

from backend.app.agents.food.tools import DeliveryMCPClient
from backend.app.core.llm import create_llm
from backend.app.main import create_app
from delivery_mcp.seed import seed
from delivery_mcp.services import DeliveryService
from shared.config import ROOT
from shared.database import database

from .models import ScriptedModel

EXACT = "麦当劳1+1套餐，双层吉士堡和无糖冰可乐"


class FlowTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        output = ROOT / ".test-output"
        output.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=output)
        cls.db_path = Path(cls.temp.name) / "test.sqlite3"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        env = {
            **os.environ,
            "LIFEOS_DB": str(cls.db_path),
            "MCP_PORT": str(cls.port),
            "DELIVERY_SCENARIO": "success",
        }
        cls.log = open(Path(cls.temp.name) / "mcp.log", "w", encoding="utf-8")
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "delivery_mcp.server"],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=cls.log,
        )

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    async def asyncSetUp(self):
        self.gateway = DeliveryMCPClient(f"http://127.0.0.1:{self.port}/mcp", timeout=3)
        for attempt in range(60):
            try:
                await self.gateway.discover()
                break
            except Exception:
                if attempt == 59 or self.process.poll() is not None:
                    raise
                await asyncio.sleep(0.1)
        self.model = ScriptedModel()
        self.app = create_app(
            client=self.gateway, db_path=self.db_path, retry_delays=(), llm=self.model
        )
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.sid = (await self.post("/api/sessions", {}))["session_id"]

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.life.__aexit__(None, None, None)

    async def post(self, url, body):
        response = await self.http.post(url, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def chat(self, message, **extra):
        return await self.post(
            "/api/food/chat",
            {
                "session_id": self.sid,
                "client_message_id": uuid.uuid4().hex,
                "message": message,
                **extra,
            },
        )

    def queue_order(self, address_id="home"):
        self.model.enqueue(
            ("list_delivery_addresses", {}),
            ("search_restaurants", {"address_id": address_id, "keyword": "麦当劳"}),
            ("get_food_details", {"food_id": "mc_combo"}),
            (
                "set_cart",
                {
                    "restaurant_id": "mc001",
                    "address_id": address_id,
                    "items": [
                        {
                            "food_id": "mc_combo",
                            "quantity": 1,
                            "selected_option_ids": ["double_cheese", "zero_coke", "normal_ice"],
                        }
                    ],
                },
            ),
        )

    async def decide(self, view, action="confirm"):
        return await self.post(
            "/api/food/resume",
            {"session_id": self.sid, "approval_id": view["approval"]["id"], "action": action},
        )

    def count_orders(self, cart_id):
        with database(self.db_path) as db:
            return db.execute(
                "SELECT count(*) FROM delivery_orders WHERE cart_id=?", (cart_id,)
            ).fetchone()[0]

    async def test_exact_confirm_and_repeated_confirm(self):
        self.assertEqual(len(self.gateway.definitions), 7)
        self.assertTrue(self.gateway.definitions["set_cart"]["outputSchema"])
        self.queue_order()
        view = await self.chat(EXACT)
        self.assertEqual(view["status"], "awaiting_confirmation")
        self.assertEqual(view["cart"]["amounts"]["total"], 1790)
        self.assertEqual(
            view["cart"]["items"][0]["selected_option_ids"],
            ["double_cheese", "zero_coke", "normal_ice"],
        )
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 0)
        done = await self.decide(view)
        self.assertEqual(done["order_result"]["outcome"], "succeeded")
        self.assertIn("模拟支付成功", done["messages"][-1]["content"])
        repeated = await self.decide(view)
        self.assertEqual(
            repeated["order_result"]["order"]["order_id"], done["order_result"]["order"]["order_id"]
        )
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)

    async def test_cancel_and_stale_confirmation(self):
        self.queue_order()
        view = await self.chat(EXACT)
        cancelled = await self.decide(view, "cancel")
        self.assertEqual(cancelled["order_result"]["outcome"], "cancelled")
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 0)
        response = await self.http.post(
            "/api/food/resume",
            json={
                "session_id": self.sid,
                "approval_id": view["approval"]["id"],
                "action": "confirm",
            },
        )
        self.assertEqual(response.status_code, 409)

    async def test_recommend_selection_and_missing_options(self):
        foods = (await self.gateway.call("get_menu", {"keyword": "汉堡"}))["foods"]
        self.model.enqueue(
            ("get_menu", {"keyword": "汉堡"}),
            ("PresentCandidates", {
                "kind": "food",
                "items": [
                    {"id": food["food_id"], "reason": "符合汉堡需求"}
                    for food in foods[:3]
                ],
            }),
        )
        view = await self.chat("给我点一个汉堡")
        self.assertEqual(len(view["candidates"]), 3)
        candidate = view["candidates"][0]
        self.model.enqueue(
            ("get_food_details", {"food_id": candidate["id"]}), "请补充主食、饮品和温度。"
        )
        selected = await self.chat(
            "选择第一个", selection={"kind": candidate["kind"], "id": candidate["id"]}
        )
        self.assertEqual(selected["status"], "idle")
        self.assertIn("主食", selected["messages"][-1]["content"])
        self.model.enqueue(
            (
                "set_cart",
                {
                    "restaurant_id": "mc001",
                    "address_id": "home",
                    "items": [
                        {
                            "food_id": "mc_combo",
                            "quantity": 1,
                            "selected_option_ids": ["double_cheese", "zero_coke", "no_ice"],
                        }
                    ],
                },
            )
        )
        complete = await self.chat("双层吉士堡，无糖可乐去冰")
        self.assertEqual(complete["status"], "awaiting_confirmation")
        self.assertIn("no_ice", complete["cart"]["items"][0]["selected_option_ids"])

    async def test_restaurant_cards_follow_structured_choice(self):
        restaurants = (await self.gateway.call(
            "search_restaurants", {"address_id": "home"}
        ))["restaurants"]
        chosen = [restaurants[1], restaurants[2]]
        self.model.enqueue(
            ("search_restaurants", {"address_id": "home"}),
            ("PresentCandidates", {
                "kind": "restaurant",
                "items": [
                    {"id": restaurant["restaurant_id"], "reason": "可配送"}
                    for restaurant in chosen
                ],
            }),
        )
        view = await self.chat("附近有什么餐厅")
        self.assertEqual(
            [card["id"] for card in view["candidates"]],
            [restaurant["restaurant_id"] for restaurant in chosen],
        )
        self.assertEqual(view["messages"][-1]["content"], "为你找到以下 2 家餐厅，请查看卡片并选择。")

    async def test_unrelated_address_and_message_deduplication(self):
        self.model.enqueue("这个需求不属于 Food Agent 的职责。")
        view = await self.chat("给我搜集新闻消息")
        self.assertIn("职责", view["messages"][-1]["content"])
        self.model.enqueue(("list_delivery_addresses", {}), "请选择配送地址。")
        view = await self.chat("选择配送地址")
        chosen = view["candidates"][1]
        self.model.enqueue("已选择办公地址。")
        await self.chat("使用办公地址", selection={"kind": "address", "id": chosen["id"]})
        rid = uuid.uuid4().hex
        body = {"session_id": self.sid, "client_message_id": rid, "message": EXACT}
        self.queue_order("office")
        first = await self.post("/api/food/chat", body)
        second = await self.post("/api/food/chat", body)
        self.assertEqual(first["cart"]["cart_id"], second["cart"]["cart_id"])
        self.assertEqual(first["cart"]["delivery_address"]["address_id"], "office")
        self.assertEqual(len(first["messages"]), len(second["messages"]))

    async def test_restart_pending_confirmation(self):
        self.queue_order()
        view = await self.chat(EXACT)
        # Recreate checkpointer/graph/service against the same file, like a process restart.
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
        restored = (await self.http.get(f"/api/sessions/{self.sid}")).json()
        self.assertEqual(restored["approval"]["id"], view["approval"]["id"])
        self.assertEqual(restored["status"], "awaiting_confirmation")
        done = await self.decide(restored)
        self.assertEqual(done["order_result"]["outcome"], "succeeded")

    async def restart_platform(self, scenario):
        cls = type(self)
        cls.process.terminate()
        cls.process.wait(timeout=10)
        env = {
            **os.environ,
            "LIFEOS_DB": str(self.db_path),
            "MCP_PORT": str(self.port),
            "DELIVERY_SCENARIO": scenario,
        }
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "delivery_mcp.server"],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=cls.log,
        )
        for attempt in range(60):
            try:
                await self.gateway.discover()
                return
            except Exception:
                if attempt == 59:
                    raise
                await asyncio.sleep(0.1)

    async def test_committed_order_before_checkpoint_recovery(self):
        self.queue_order()
        view = await self.chat(EXACT)
        approval = self.app.state.repository.decide(self.sid, view["approval"]["id"], "confirm")
        existing = await self.gateway.call(
            "submit_order",
            {
                "cart_id": view["cart"]["cart_id"],
                "cart_version": view["cart"]["cart_version"],
                "idempotency_key": approval["idempotency_key"],
            },
        )
        # The order is durable, but graph still points at the approval interrupt.
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
        restored = (await self.http.get(f"/api/sessions/{self.sid}")).json()
        self.assertEqual(
            restored["order_result"]["order"]["order_id"], existing["order"]["order_id"]
        )
        self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)

    async def test_submit_fault_resolution_and_manual_refresh(self):
        try:
            for scenario, expected in [
                ("lost_response", "succeeded"),
                ("rejected", "rejected"),
                ("unknown", "unknown"),
                ("pending", "pending"),
            ]:
                with self.subTest(scenario=scenario):
                    await self.restart_platform(scenario)
                    self.sid = (await self.post("/api/sessions", {}))["session_id"]
                    self.queue_order()
                    view = await self.chat(EXACT)
                    done = await self.decide(view)
                    self.assertEqual(done["order_result"]["outcome"], expected)
                    self.assertEqual(self.count_orders(view["cart"]["cart_id"]), 1)
                    if expected in {"pending", "unknown"}:
                        self.assertEqual(done["status"], "order_pending")
                        if scenario == "pending":
                            await asyncio.sleep(2.1)
                        refreshed = await self.post(
                            f"/api/food/sessions/{self.sid}/order/refresh", {}
                        )
                        self.assertEqual(
                            refreshed["order_result"]["outcome"],
                            "succeeded" if scenario == "pending" else "unknown",
                        )
        finally:
            await self.restart_platform("success")

    async def test_model_tool_loop_uses_mcp_and_interrupt(self):
        self.queue_order()
        view = await self.chat(EXACT)
        self.assertEqual(view["status"], "awaiting_confirmation")
        exposed = {
            t["function"]["name"] if isinstance(t, dict) else t.__name__
            for t in self.model.tools
        }
        self.assertNotIn("submit_order", exposed)
        self.assertNotIn("get_order", exposed)
        for messages in self.model.calls[1:]:
            self.assertIsInstance(messages[-1], ToolMessage)
        self.assertFalse(self.model.steps)
        done = await self.decide(view)
        self.assertEqual(done["order_result"]["outcome"], "succeeded")

    async def test_missing_key_fails_startup_without_fallback(self):
        with patch("backend.app.core.llm.settings.openai_api_key", ""):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                create_llm()
            app = create_app(client=self.gateway, db_path=self.db_path)
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                async with app.router.lifespan_context(app):
                    self.fail("Application must require a configured model")

    async def test_model_failure_does_not_make_fake_order(self):
        with patch.object(
            self.model, "ainvoke", side_effect=RuntimeError("test model unavailable")
        ):
            with self.assertLogs("backend.app.agents.food.nodes", level="ERROR"):
                view = await self.chat(EXACT)
        self.assertIsNone(view["cart"])
        self.assertIsNone(view["approval"])
        self.assertIsNone(view["order_result"])
        self.assertIn("模型服务暂时不可用", view["messages"][-1]["content"])


class PlatformTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / ".test-output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.path = Path(self.temp.name) / "platform.sqlite3"
        seed(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def cart(self, service):
        return service.set_cart(
            "mc001",
            "home",
            [
                {
                    "food_id": "mc_combo",
                    "quantity": 1,
                    "selected_option_ids": ["double_cheese", "zero_coke", "no_ice"],
                }
            ],
        )

    def test_idempotency_and_conflicting_payload(self):
        service = DeliveryService(self.path)
        cart = self.cart(service)
        first = service.submit(cart.cart_id, cart.cart_version, "same-key")
        repeated = service.submit(cart.cart_id, cart.cart_version, "same-key")
        self.assertEqual(first.order.order_id, repeated.order.order_id)
        other = self.cart(service)
        with self.assertRaises(ValueError):
            service.submit(other.cart_id, other.cart_version, "same-key")
        with self.assertRaises(ValueError):
            service.submit(cart.cart_id, cart.cart_version, "new-key")

    def test_cart_replacement_and_invalid_specs(self):
        service = DeliveryService(self.path)
        cart = self.cart(service)
        replaced = service.set_cart(
            "mc001", "home", [{"food_id": "mc_burger", "quantity": 2}], cart_id=cart.cart_id
        )
        self.assertEqual(replaced.cart_version, 2)
        self.assertEqual(len(replaced.items), 1)
        self.assertEqual(replaced.items[0].quantity, 2)
        with self.assertRaises(ValueError):
            service.set_cart("mc001", "home", [{"food_id": "mc_combo", "quantity": 1}])

    def test_fault_scenarios(self):
        for scenario in ["rejected", "payment_failed", "lost_response", "unknown"]:
            with self.subTest(scenario=scenario):
                service = DeliveryService(self.path, scenario)
                cart = self.cart(service)
                result = service.submit(cart.cart_id, cart.cart_version, scenario)
                queried = service.get_order(idempotency_key=scenario)
                if scenario == "lost_response":
                    self.assertEqual(result.outcome, "unknown")
                    self.assertEqual(queried.outcome, "succeeded")
                elif scenario == "unknown":
                    self.assertFalse(queried.found)
                    self.assertEqual(queried.outcome, "unknown")
                else:
                    self.assertEqual(result.outcome, "rejected")
                    self.assertEqual(queried.order.payment_status, "failed")


if __name__ == "__main__":
    unittest.main()
