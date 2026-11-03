import copy
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema

from backend.app.agents.food.tools import MODEL_TOOLS, DeliveryMCPClient


class ToolDefinitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = DeliveryMCPClient("http://unused.invalid")
        self.definition = {
            "description": "查询餐品，金额单位为人民币分。",
            "inputSchema": {
                "type": "object",
                "properties": {"food_id": {"type": "string"}},
                "required": ["food_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"food_id": {"type": "string"}},
                "required": ["food_id"],
                "additionalProperties": False,
            },
        }
        self.client.definitions = {
            name: copy.deepcopy(self.definition)
            for name in MODEL_TOOLS | {"submit_order", "get_order"}
        }

    async def test_model_contract_excludes_outputs_without_changing_mcp_metadata(self):
        original = copy.deepcopy(self.client.definitions)
        tools = self.client.model_definitions()
        self.assertEqual({t["function"]["name"] for t in tools}, MODEL_TOOLS)
        for tool in tools:
            function = tool["function"]
            self.assertEqual(set(function), {"name", "description", "parameters"})
            self.assertEqual(function["description"], self.definition["description"])
            self.assertEqual(function["parameters"], self.definition["inputSchema"])
            self.assertNotIn("outputSchema", function)
            self.assertNotIn("输出结构及字段说明", function["description"])
        self.assertEqual(self.client.definitions, original)

    async def test_mcp_output_validation_still_rejects_invalid_results(self):
        session = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=SimpleNamespace(
                    isError=False, structuredContent={"food_id": 123}, content=[]
                )
            )
        )

        @asynccontextmanager
        async def open_session():
            yield session

        self.client.session = open_session
        with self.assertRaises(jsonschema.ValidationError):
            await self.client._call("get_food_details", {"food_id": "burger"})
        session.call_tool.return_value.structuredContent = {"food_id": "burger"}
        self.assertEqual(
            await self.client._call("get_food_details", {"food_id": "burger"}),
            {"food_id": "burger"},
        )
        with self.assertRaises(jsonschema.ValidationError):
            await self.client._call("get_food_details", {"food_id": 123})
        self.assertEqual(session.call_tool.await_count, 2)
