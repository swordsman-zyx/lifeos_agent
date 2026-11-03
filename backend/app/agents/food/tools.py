"""MCP metadata is the source of truth for business tool definitions."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ...core.tracing import traced_tool

QUERY_TOOLS = {"list_delivery_addresses", "search_restaurants", "get_menu", "get_food_details"}
MODEL_TOOLS = QUERY_TOOLS | {"set_cart"}


class ToolFailure(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class DeliveryMCPClient:
    def __init__(self, url, timeout=15):
        self.url, self.timeout = url, timeout
        self.definitions = {}

    @asynccontextmanager
    async def session(self):
        # One MCP lifetime per operation; AnyIO contexts are opened/closed in the same task.
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as http:
            async with streamable_http_client(self.url, http_client=http) as (read, write, _):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=self.timeout)
                ) as session:
                    await session.initialize()
                    yield session

    async def discover(self):
        async with self.session() as session:
            result = await session.list_tools()
        self.definitions = {tool.name: tool.model_dump(by_alias=True) for tool in result.tools}
        required = MODEL_TOOLS | {"submit_order", "get_order"}
        if not required <= self.definitions.keys():
            raise ToolFailure("MCP 缺少所需外卖工具")

    async def call(self, name, arguments):
        attempts = 2 if name in QUERY_TOOLS else 1
        for attempt in range(attempts):
            try:
                return await traced_tool(name, arguments, attempt + 1, self._call(name, arguments))
            except ToolFailure as exc:
                if not exc.retryable or attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.25)

    async def _call(self, name, arguments):
        if not self.definitions:
            await self.discover()
        if name not in self.definitions:
            raise ToolFailure(f"未知工具：{name}")
        definition = self.definitions[name]
        jsonschema.validate(arguments, definition["inputSchema"])
        try:
            async with self.session() as session:
                result = await session.call_tool(name, arguments)
            if result.isError:
                raise ToolFailure("；".join(c.text for c in result.content if c.type == "text"))
            data = result.structuredContent
            if data is None:
                data = json.loads(next(c.text for c in result.content if c.type == "text"))
            if definition.get("outputSchema"):
                jsonschema.validate(data, definition["outputSchema"])
            return data
        except (ToolFailure, jsonschema.ValidationError):
            raise
        except Exception as exc:
            raise ToolFailure(f"{name} 调用未完成，请稍后重试。", retryable=True) from exc

    def model_definitions(self):
        """Expose callable inputs; keep output contracts for client validation."""
        definitions = []
        for name, definition in self.definitions.items():
            if name not in MODEL_TOOLS:
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": definition.get("description", ""),
                        "parameters": definition["inputSchema"],
                    },
                }
            )
        return definitions
