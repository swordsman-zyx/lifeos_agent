import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import requests
from langchain_openai import ChatOpenAI
from langsmith import Client

from backend.app.core.redaction import clean
from backend.app.main import create_app


def capture_client():
    sent = []

    def respond(method, url, **kwargs):
        data = kwargs.get("data")
        if data:
            sent.append({**json.loads(data), "_method": method.upper()})
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    session = requests.Session()
    session.request = Mock(side_effect=respond)
    client = Client(
        api_url="https://smith.invalid",
        api_key="test-only",
        session=session,
        info={},
        auto_batch_tracing=False,
        hide_inputs=clean,
        hide_outputs=clean,
        hide_metadata=clean,
        run_ops_buffer_size=100,
        run_ops_buffer_timeout_ms=1000,
        process_buffered_run_ops=lambda ops: [clean(op) for op in ops],
    )
    return client, sent


class Gateway:
    definitions = {}

    async def discover(self):
        pass


class TraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_client_masks_batch_ids_and_can_disable(self):
        from uuid import uuid4

        from pydantic import SecretStr

        from backend.app.core.tracing import create_trace_client
        from shared.config import settings

        with patch.object(settings, "langsmith_tracing", False):
            self.assertIsNone(create_trace_client())
        with (
            patch.object(settings, "langsmith_tracing", True),
            patch.object(settings, "langsmith_api_key", SecretStr("unit-test-secret")),
            patch("backend.app.core.tracing.Client") as constructor,
        ):
            create_trace_client()
            options = constructor.call_args.kwargs
            ident = uuid4()
            result = options["process_buffered_run_ops"](
                [
                    {
                        "id": ident,
                        "error": "unit-test-secret",
                        "inputs": {"phone": "13812345678", "api_key": "sensitive"},
                    }
                ]
            )[0]
            self.assertEqual(result["id"], ident)
            self.assertNotIn("unit-test-secret", result["error"])
            self.assertNotIn("13812345678", str(result))
            self.assertGreater(options["run_ops_buffer_size"], 0)

    async def test_cloud_upload_failure_does_not_fail_business(self):
        from uuid import uuid4

        from langchain_core.runnables import RunnableLambda
        from langsmith import tracing_context

        from backend.app.core.tracing import request_config
        from backend.app.repositories.usage import UsageRepository

        cloud, _ = capture_client()
        with tempfile.TemporaryDirectory() as directory:
            usage = UsageRepository(Path(directory) / "test.sqlite3")
            with (
                patch.object(
                    Client, "create_run", side_effect=RuntimeError("simulated upload failure")
                ),
                patch.object(
                    Client, "update_run", side_effect=RuntimeError("simulated upload failure")
                ),
                tracing_context(enabled=True, client=cloud),
            ):
                result = await RunnableLambda(lambda _: {"ok": True}).ainvoke(
                    {}, config=request_config(usage, cloud, str(uuid4()), "sid", "op")
                )
            self.assertEqual(result, {"ok": True})
        cloud.close()

    async def test_tools_subgraph_interrupt_resume_and_redaction(self):
        from uuid import uuid4

        from langchain_core.runnables import RunnableLambda
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt
        from langsmith import tracing_context

        from backend.app.core.tracing import graph_config, request_config, trace_scope, traced_tool
        from backend.app.repositories.usage import UsageRepository

        cloud, sent = capture_client()

        async def tools(state):
            async def fail():
                raise ValueError("temporary")

            async def success():
                return {"phone": "13812345678", "address": "上海市示例路1号", "items": ["burger"]}

            with self.assertRaises(ValueError):
                await traced_tool("get_menu", {"restaurant_id": "test"}, 1, fail())
            await traced_tool("get_menu", {"restaurant_id": "test"}, 2, success())
            return state

        def approval(state):
            action = interrupt({"approval_id": "test-approval"})
            return {"action": action}

        child = StateGraph(dict)
        child.add_node("tools", tools)
        child.add_node("approval", approval)
        child.add_edge(START, "tools")
        child.add_edge("tools", "approval")
        child.add_edge("approval", END)
        parent = StateGraph(dict)
        parent.add_node("food", child.compile(checkpointer=True))
        parent.add_edge(START, "food")
        parent.add_edge("food", END)
        graph = parent.compile(checkpointer=InMemorySaver())
        with tempfile.TemporaryDirectory() as directory:
            usage = UsageRepository(Path(directory) / "usage.sqlite3")
            for value in ({"message": "demo"}, Command(resume="cancel")):
                trace_id = str(uuid4())
                meta = {
                    "trace_id": trace_id,
                    "session_id": "sid",
                    "usage_operation_id": "approval:test",
                }
                token = trace_scope.set(meta)

                async def execute(_):
                    return await graph.ainvoke(
                        value, graph_config({"configurable": {"thread_id": "sid"}})
                    )

                try:
                    with tracing_context(enabled=True, client=cloud):
                        result = await RunnableLambda(execute).ainvoke(
                            {}, request_config(usage, cloud, trace_id, "sid", "approval:test")
                        )
                finally:
                    trace_scope.reset(token)
            self.assertEqual(result["action"], "cancel")
            self.assertEqual(usage.account()["total_tokens"], 0)
        cloud.flush()
        cloud.close()
        creates = [p for p in sent if p.get("run_type") and p["_method"] == "POST"]
        tool_runs = [p for p in creates if p["run_type"] == "tool"]
        self.assertEqual(len(tool_runs), 2)
        self.assertEqual({p["extra"]["metadata"]["mcp_attempt"] for p in tool_runs}, {1, 2})
        ids = {p["id"] for p in creates}
        self.assertTrue(all(p["parent_run_id"] in ids for p in tool_runs))
        for run in creates:
            self.assertEqual(run["extra"]["metadata"]["session_id"], "sid")
        for secret in ("13812345678", "上海市示例路1号"):
            self.assertNotIn(secret, json.dumps(sent, ensure_ascii=False))
        self.assertTrue(any(p.get("error") for p in sent))

    async def test_structured_route_usage_through_api_and_checkpoint(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertIs(body["enable_thinking"], False)
            name = body["tools"][0]["function"]["name"]
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen-test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-test",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(
                                                {"intent": "unsupported", "reply": "你好"}
                                            ),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38},
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
                model = ChatOpenAI(
                    model="qwen-test",
                    api_key="test-only",
                    http_async_client=transport,
                    extra_body={"enable_thinking": False},
                )
                cloud, sent = capture_client()
                app = create_app(
                    client=Gateway(),
                    db_path=Path(directory) / "test.sqlite3",
                    llm=model,
                    tracing_client=cloud,
                )
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        sid = (await client.post("/api/sessions", json={})).json()["session_id"]
                        body = {"session_id": sid, "client_message_id": "hello", "message": "你好"}
                        response = await client.post("/api/lifeos/chat", json=body)
                        self.assertEqual(response.status_code, 200, response.text)
                        view = response.json()
                        self.assertEqual(view["usage"]["account"]["total_tokens"], 38)
                        usage = view["messages"][-1]["usage"]
                        self.assertEqual(usage["agents"]["lifeos"]["total_tokens"], 38)
                        await client.post("/api/lifeos/chat", json=body)
                        self.assertEqual(len(requests), 1)
                        restored = (await client.get("/api/sessions/" + sid)).json()
                        self.assertEqual(restored["messages"][-1]["usage"]["total_tokens"], 38)
                        # Older checkpoints and unused parent-state channels may
                        # contain explicit null, rather than an absent key.
                        await app.state.service.graph.aupdate_state(
                            app.state.service.config(sid), {"candidates": None}
                        )
                        restored = (await client.get("/api/sessions/" + sid)).json()
                        self.assertEqual(restored["candidates"], [])
                        rejected = await client.post(
                            "/api/lifeos/chat",
                            json={
                                **body,
                                "client_message_id": "old-selection",
                                "selection": {"kind": "food", "id": "missing"},
                            },
                        )
                        self.assertEqual(rejected.status_code, 409)
                        self.assertEqual(len(requests), 1)
        creates = [p for p in sent if p.get("run_type") and p["_method"] == "POST"]
        llms = [p for p in creates if p["run_type"] == "llm"]
        self.assertEqual(len(llms), 1, [(v.get("id"), v.get("name")) for v in llms])
        self.assertIn("RouteDecision", json.dumps(llms[0]))
        self.assertTrue(llms[0].get("parent_run_id"))
        roots = [p for p in creates if p.get("name") == "LifeOS.request"]
        self.assertTrue(roots)
        self.assertEqual(roots[0]["extra"]["metadata"]["session_id"], sid)
