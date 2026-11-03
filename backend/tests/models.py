"""Explicit model responses for orchestration tests; never imported by application code."""

import uuid
from collections import defaultdict, deque

from langchain_core.messages import AIMessage


class ScriptedModel:
    def __init__(self):
        self.steps = deque()
        self.tools = []
        self.calls = []
        self.structured = defaultdict(deque)
        self.structured_calls = []

    def enqueue(self, *steps):
        self.steps.extend(steps)

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def enqueue_structured(self, schema_name, *responses):
        self.structured[schema_name].extend(responses)

    def with_structured_output(self, schema, **kwargs):
        model = self

        class Responder:
            async def ainvoke(self, messages):
                model.structured_calls.append((schema.__name__, messages))
                if not model.structured[schema.__name__]:
                    raise AssertionError("Missing explicit test response for " + schema.__name__)
                return schema.model_validate(model.structured[schema.__name__].popleft())

        return Responder()

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self.steps:
            raise AssertionError("Test must explicitly supply every model response")
        step = self.steps.popleft()
        if isinstance(step, str):
            return AIMessage(content=step)
        name, args = step
        return AIMessage(
            content="", tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}]
        )
