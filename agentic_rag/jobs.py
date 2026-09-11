"""SQLite-backed registry for OpenViking command jobs."""

from __future__ import annotations

import asyncio
from asyncio import Lock
from collections.abc import Mapping
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


class JobNotFoundError(KeyError):
    """A requested command job does not exist."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class SqliteJobStore:
    """Durable local job registry stored beside persistent sessions."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._initialized = False
        self._initialize_lock = Lock()

    async def create(
        self,
        *,
        job_id: str,
        request_id: str,
        command: str,
        arguments: Mapping[str, Any],
        status: str,
        openviking_task_id: str | None = None,
        result: Any = None,
        error: Any = None,
    ) -> dict[str, Any]:
        now = _timestamp()
        await self._upsert(
            job_id,
            request_id,
            command,
            arguments,
            status,
            openviking_task_id,
            result,
            error,
            created_at=now,
            updated_at=now,
        )
        return await self.get(job_id)

    async def startup(self) -> None:
        await self._ready()

    async def get(self, job_id: str) -> dict[str, Any]:
        await self._ready()
        row = await asyncio.to_thread(self._get_sync, job_id)
        if row is None:
            raise JobNotFoundError(job_id)
        return row

    async def mark_running(self, job_id: str) -> dict[str, Any]:
        return await self._update_status(job_id, "running", None, None)

    async def succeed(self, job_id: str, result: Any) -> dict[str, Any]:
        return await self._update_status(job_id, "succeeded", result, None)

    async def fail(self, job_id: str, error: dict[str, Any]) -> dict[str, Any]:
        return await self._update_status(job_id, "failed", None, error)

    async def interrupt(self, job_id: str, error: dict[str, Any]) -> dict[str, Any]:
        return await self._update_status(job_id, "interrupted", None, error)

    async def refresh(
        self,
        job_id: str,
        *,
        status: str,
        result: Any,
        openviking_task_id: str | None,
    ) -> dict[str, Any]:
        await self._ready()
        await asyncio.to_thread(
            self._refresh_sync,
            job_id,
            status,
            result,
            openviking_task_id,
        )
        return await self.get(job_id)

    def _refresh_sync(
        self,
        job_id: str,
        status: str,
        result: Any,
        openviking_task_id: str | None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE command_jobs
                SET status = ?, result_json = ?, openviking_task_id = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    _dump(result),
                    openviking_task_id,
                    _timestamp(),
                    job_id,
                ),
            )

    async def _update_status(
        self, job_id: str, status: str, result: Any, error: Any
    ) -> dict[str, Any]:
        await self._ready()
        await asyncio.to_thread(
            self._update_status_sync, job_id, status, result, error
        )
        return await self.get(job_id)

    def _update_status_sync(
        self, job_id: str, status: str, result: Any, error: Any
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE command_jobs
                SET status = ?, result_json = ?, error_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    _dump(result),
                    _dump(error),
                    _timestamp(),
                    job_id,
                ),
            )

    async def _upsert(
        self,
        job_id: str,
        request_id: str,
        command: str,
        arguments: Mapping[str, Any],
        status: str,
        openviking_task_id: str | None,
        result: Any,
        error: Any,
        *,
        created_at: str,
        updated_at: str,
    ) -> None:
        await self._ready()
        await asyncio.to_thread(
            self._upsert_sync,
            job_id,
            request_id,
            command,
            arguments,
            status,
            openviking_task_id,
            result,
            error,
            created_at,
            updated_at,
        )

    def _upsert_sync(
        self,
        job_id: str,
        request_id: str,
        command: str,
        arguments: Mapping[str, Any],
        status: str,
        openviking_task_id: str | None,
        result: Any,
        error: Any,
        created_at: str,
        updated_at: str,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO command_jobs (
                    job_id, request_id, command, arguments_json, status,
                    openviking_task_id, result_json, error_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request_id,
                    command,
                    _dump(dict(arguments)),
                    status,
                    openviking_task_id,
                    _dump(result),
                    _dump(error),
                    created_at,
                    updated_at,
                ),
            )

    async def _ready(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if not self._initialized:
                await asyncio.to_thread(self._initialize)
                self._initialized = True

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS command_jobs (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    openviking_task_id TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                UPDATE command_jobs
                SET status = 'interrupted',
                    error_json = '{"code":"JOB_INTERRUPTED","message":"Local command job was interrupted by application restart"}',
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                  AND openviking_task_id IS NULL;
                """,
                (_timestamp(),),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _get_sync(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT job_id, request_id, command, arguments_json, status,
                       openviking_task_id, result_json, error_json,
                       created_at, updated_at
                FROM command_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_job(row)


def _row_to_job(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "job_id": row[0],
        "request_id": row[1],
        "command": row[2],
        "arguments": json.loads(row[3]),
        "status": row[4],
        "openviking_task_id": row[5],
        "result": json.loads(row[6]) if row[6] is not None else None,
        "error": json.loads(row[7]) if row[7] is not None else None,
        "created_at": row[8],
        "updated_at": row[9],
    }

