import os
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/agent.db"


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        self._create_parent_directory()
        self.engine = create_async_engine(self.url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        event.listen(self.engine.sync_engine, "connect", self._enable_foreign_keys)

    def _create_parent_directory(self) -> None:
        database_path = make_url(self.url).database
        if database_path and database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _enable_foreign_keys(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(text("PRAGMA journal_mode=WAL"))
            await connection.run_sync(Base.metadata.create_all)

    def session(self) -> AsyncSession:
        return self.sessions()

    async def close(self) -> None:
        await self.engine.dispose()
