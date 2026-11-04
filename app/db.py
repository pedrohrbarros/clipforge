"""Postgres (Supabase) through SQLAlchemy's async ORM: users and their jobs."""

import datetime as dt

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.auth import User
from app.models import Base, JobRow, UserRow


def today_start() -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _async_url(url: str) -> str:
    # Accept the plain postgresql:// string Supabase shows and pin the psycopg driver.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Db:
    def __init__(self, url: str):
        self.enabled = bool(url)
        self._engine: AsyncEngine | None = (
            create_async_engine(_async_url(url), pool_pre_ping=True, pool_size=3, max_overflow=2)
            if url
            else None
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_tables(self) -> None:
        if self._engine is None:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def upsert_user(self, user: User) -> None:
        if not self.enabled:
            return
        stmt = insert(UserRow).values(id=user.id, email=user.email, name=user.name)
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserRow.id],
            set_={"email": user.email, "name": user.name, "last_seen": func.now()},
        )
        async with self._sessions.begin() as session:
            await session.execute(stmt)

    async def jobs_today(self, user_id: str) -> int:
        """Jobs this user started since midnight UTC that did not fail."""
        if not self.enabled:
            return 0
        stmt = select(func.count(JobRow.id)).where(
            JobRow.user_id == user_id,
            JobRow.status != "failed",
            JobRow.created_at >= today_start(),
        )
        async with self._sessions() as session:
            return int(await session.scalar(stmt) or 0)

    async def insert_job(self, row: dict) -> None:
        if not self.enabled:
            return
        async with self._sessions.begin() as session:
            session.add(JobRow(**row))

    async def update_job(self, job_id: str, patch: dict) -> None:
        if not self.enabled:
            return
        allowed = {"status", "progress", "message", "code", "shorts", "zip_url"}
        allowed |= {"finished_at", "email_sent"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if not fields:
            return
        async with self._sessions.begin() as session:
            await session.execute(update(JobRow).where(JobRow.id == job_id).values(**fields))

    async def get_job(self, job_id: str) -> dict | None:
        if not self.enabled:
            return None
        async with self._sessions() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "user_id": row.user_id,
                "status": row.status,
                "progress": row.progress,
                "message": row.message,
                "code": row.code,
                "shorts": row.shorts or [],
                "zip_url": row.zip_url,
            }
