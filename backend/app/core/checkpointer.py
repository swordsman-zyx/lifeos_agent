from contextlib import asynccontextmanager

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_checkpointer(path):
    async with aiosqlite.connect(str(path), timeout=15) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=15000")
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        yield saver
