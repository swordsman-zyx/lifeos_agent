import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from backend.app.core.usage import UsageCallback, usage_scope
from backend.app.repositories.usage import UsageRepository


class UsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "usage.sqlite3"
        self.repo = UsageRepository(self.path)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_official_callback_and_thinking_request(self):
        import json

        def respond(request):
            body = json.loads(request.content)
            self.assertIs(body["enable_thinking"], False)
            return httpx.Response(
                200,
                json={
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 25, "total_tokens": 55},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ChatOpenAI(
                api_key="test-only",
                model="qwen-test",
                base_url="https://test.invalid/v1",
                http_async_client=client,
                extra_body={"enable_thinking": False},
                callbacks=[UsageCallback(self.repo)],
            )
            token = usage_scope.set(("op", "session"))
            try:
                from langgraph.graph import END, START, StateGraph

                async def assistant(state):
                    await model.ainvoke([HumanMessage(content="test")])
                    return state

                child = StateGraph(dict)
                child.add_node("assistant", assistant)
                child.add_edge(START, "assistant")
                child.add_edge("assistant", END)
                parent = StateGraph(dict)
                parent.add_node("food", child.compile())
                parent.add_edge(START, "food")
                parent.add_edge("food", END)
                await parent.compile().ainvoke({"message": "test"})
            finally:
                usage_scope.reset(token)
        self.repo.finish_operation("op", "answer")
        account = self.repo.account()
        self.assertEqual(account["total_tokens"], 55)
        self.assertNotIn("remaining", account)
        self.assertEqual(account["agents"]["food"]["total_tokens"], 55)
        view = self.repo.decorate({"session_id": "session", "messages": [{"id": "answer"}]})
        self.assertEqual(view["messages"][0]["usage"]["session_total"], 55)

    async def test_dedup_unknown_restart_and_shared_budget(self):
        ident = str(uuid4())
        self.repo.start_call(ident, "a", "one", "lifeos", "test")
        self.repo.finish_call(ident, {"input_tokens": 10, "output_tokens": 5})
        self.repo.start_call(ident, "a", "one", "lifeos", "test")
        self.repo.finish_call(ident, {"input_tokens": 99, "output_tokens": 99})
        self.repo.start_call("pending", "b", "two", "memory", "test")
        reopened = UsageRepository(self.path)
        self.assertEqual(reopened.account()["total_tokens"], 15)
        self.assertEqual(reopened.account()["unknown_calls"], 1)
        self.assertEqual(reopened.summary(session_id="two")["unknown_calls"], 1)
        reopened.begin("zero", "two")
        reopened.finish_operation("zero", "no-model")
        view = reopened.decorate({"session_id": "two", "messages": [{"id": "no-model"}]})
        self.assertEqual(view["messages"][0]["usage"]["calls"], 0)
        # A retry after a failed interaction adds new calls without changing
        # already recorded calls; replaying a completed HTTP request adds none.
        reopened.start_call("retry", "zero", "two", "food", "test")
        reopened.finish_call("retry", {"input_tokens": 2, "output_tokens": 3})
        reopened.finish_operation("zero", "no-model")
        view = reopened.decorate({"session_id": "two", "messages": [{"id": "no-model"}]})
        self.assertEqual(view["messages"][0]["usage"]["total_tokens"], 5)
        self.assertEqual(view["messages"][0]["usage"]["session_total"], 5)


if __name__ == "__main__":
    unittest.main()
