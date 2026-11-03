import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .agents.food.context import FoodContext
from .agents.food.service import FoodAgentService
from .agents.food.tools import DeliveryMCPClient
from .agents.food.workflow import build_graph
from .agents.lifeos.context import LifeOSContext
from .agents.lifeos.service import LifeOSService
from .agents.lifeos.workflow import build_graph as build_lifeos_graph
from .api.routes import router
from .core.checkpointer import open_checkpointer
from .core.config import ROOT, settings
from .core.llm import create_llm
from .core.tracing import create_trace_client
from .repositories.memory import MemoryRepository
from .repositories.orders import OrderRepository
from .repositories.sessions import SessionRepository
from .repositories.usage import UsageRepository


def create_app(client=None, db_path=None, retry_delays=(1, 2, 4), *, llm=None, tracing_client=None):
    path = db_path or settings.db_path

    @asynccontextmanager
    async def lifespan(app):
        usage = UsageRepository(path)
        app.state.usage = usage
        model = llm if llm is not None else create_llm()
        # Injected test models never upload data unless a tracing client is explicitly supplied.
        app.state.tracing_client = tracing_client or (
            create_trace_client() if llm is None else None
        )
        repository = SessionRepository(path)
        orders = OrderRepository(path)
        memory = MemoryRepository(path)
        mcp_client = client or DeliveryMCPClient(settings.mcp_url, settings.mcp_timeout_seconds)
        await mcp_client.discover()
        context = FoodContext(model, mcp_client, repository, retry_delays)
        async with open_checkpointer(path) as saver:
            graph = build_graph(saver)
            legacy = FoodAgentService(graph, repository, context)
            lifeos_context = LifeOSContext(
                llm=model,
                client=mcp_client,
                repository=repository,
                retry_delays=retry_delays,
                memory=memory,
                orders=orders,
            )
            app.state.repository, app.state.mcp = repository, mcp_client
            app.state.legacy_service = legacy
            app.state.service = LifeOSService(
                build_lifeos_graph(saver), repository, lifeos_context, legacy
            )
            try:
                yield
            finally:
                if app.state.tracing_client is not None:
                    await asyncio.to_thread(app.state.tracing_client.flush, timeout=5)
                    await asyncio.to_thread(app.state.tracing_client.close, timeout=5)

    app = FastAPI(title="LifeOS Agent", version="0.3.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
