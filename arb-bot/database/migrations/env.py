"""Alembic environment. The database URL comes from DATABASE_URL (same setting the bot uses)."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from arbbot.config.settings import get_settings
from arbbot.database.models import Base

target_metadata = Base.metadata
URL = get_settings().database_url


def run_offline() -> None:
    context.configure(url=URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
