"""SQLite-backed Claude Agent SDK session storage."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, NotRequired, TypedDict
import unicodedata

try:
    from claude_agent_sdk import fold_session_summary as _fold_session_summary
except ImportError:
    _fold_session_summary = None


_MAX_SANITIZED_LENGTH = 200
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")
_LAST_WINS_FIELDS = {
    "customTitle": "custom_title",
    "aiTitle": "ai_title",
    "lastPrompt": "last_prompt",
    "summary": "summary_hint",
    "gitBranch": "git_branch",
}


class SessionKey(TypedDict):
    project_key: str
    session_id: str
    subpath: NotRequired[str]


class SessionStoreListEntry(TypedDict):
    session_id: str
    mtime: int


class SessionSummaryEntry(TypedDict):
    session_id: str
    mtime: int
    data: dict[str, Any]


SessionStoreEntry = dict[str, Any]


class SessionNotFoundError(KeyError):
    """A requested persistent conversation session does not exist."""


def _simple_hash(value: str) -> str:
    hashed = 0
    for character in value:
        hashed = (hashed << 5) - hashed + ord(character)
        hashed &= 0xFFFFFFFF
        if hashed >= 0x80000000:
            hashed -= 0x100000000
    hashed = abs(hashed)
    if hashed == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result: list[str] = []
    while hashed:
        result.append(digits[hashed % 36])
        hashed //= 36
    return "".join(reversed(result))


def _sanitize_path(name: str) -> str:
    sanitized = _SANITIZE_RE.sub("-", name)
    if len(sanitized) <= _MAX_SANITIZED_LENGTH:
        return sanitized
    return f"{sanitized[:_MAX_SANITIZED_LENGTH]}-{_simple_hash(name)}"


def project_key_for_directory(directory: Path | str | None = None) -> str:
    """Return the filesystem-safe project key used by the Claude SDK."""
    selected = Path(directory) if directory is not None else Path.cwd()
    canonical = unicodedata.normalize("NFC", os.path.realpath(selected))
    return _sanitize_path(str(canonical))


SESSION_PROJECT_KEY = (
    os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME") or project_key_for_directory()
)


def _iso_to_epoch_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return None


def _entry_text_blocks(entry: dict[str, Any]) -> list[str]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]


def _fold_first_prompt(data: dict[str, Any], entry: dict[str, Any]) -> None:
    if data.get("first_prompt_locked") or entry.get("type") != "user":
        return
    if entry.get("isMeta") is True or entry.get("isCompactSummary") is True:
        return
    message = entry.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list) and any(
            block.get("type") == "tool_result"
            for block in content
            if isinstance(block, dict)
        ):
            return
    for raw in _entry_text_blocks(entry):
        result = raw.replace("\n", " ").strip()
        if not result:
            continue
        if result.startswith("/"):
            command = result.split()[0].lstrip("/")
            data.setdefault("command_fallback", command)
            continue
        if result.startswith(("<command-name>", "Caveat:")):
            continue
        if len(result) > 200:
            result = result[:200].rstrip() + "…"
        data["first_prompt"] = result
        data["first_prompt_locked"] = True
        return


def _fallback_fold_session_summary(
    previous: SessionSummaryEntry | None,
    key: Mapping[str, str],
    entries: Sequence[SessionStoreEntry],
) -> SessionSummaryEntry:
    if previous is not None:
        summary: SessionSummaryEntry = {
            "session_id": previous["session_id"],
            "mtime": previous["mtime"],
            "data": dict(previous["data"]),
        }
    else:
        summary = {"session_id": key["session_id"], "mtime": 0, "data": {}}
    data = summary["data"]
    for entry in entries:
        epoch_ms = _iso_to_epoch_ms(entry.get("timestamp"))
        data.setdefault("is_sidechain", entry.get("isSidechain") is True)
        if epoch_ms is not None:
            data.setdefault("created_at", epoch_ms)
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd:
            data.setdefault("cwd", cwd)
        _fold_first_prompt(data, entry)
        for source, destination in _LAST_WINS_FIELDS.items():
            value = entry.get(source)
            if isinstance(value, str):
                data[destination] = value
        if entry.get("type") == "tag":
            tag = entry.get("tag")
            if isinstance(tag, str) and tag:
                data["tag"] = tag
            else:
                data.pop("tag", None)
    return summary


if _fold_session_summary is None:
    _fold_session_summary = _fallback_fold_session_summary


class SqliteSessionStore:
    """Custom Claude Agent SDK SessionStore backed by a local SQLite database."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def append(
        self, key: SessionKey, entries: Sequence[SessionStoreEntry]
    ) -> None:
        await self._ready()
        await asyncio.to_thread(self._append_sync, key, list(entries))

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        await self._ready()
        return await asyncio.to_thread(self._load_sync, key)

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        await self._ready()
        return await asyncio.to_thread(self._list_sessions_sync, project_key)

    async def list_session_summaries(
        self, project_key: str
    ) -> list[SessionSummaryEntry]:
        await self._ready()
        return await asyncio.to_thread(self._list_session_summaries_sync, project_key)

    async def delete(self, key: SessionKey) -> None:
        await self._ready()
        await asyncio.to_thread(self._delete_sync, key)

    async def list_subkeys(self, key: SessionKey) -> list[str]:
        await self._ready()
        return await asyncio.to_thread(self._list_subkeys_sync, key)

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
                CREATE TABLE IF NOT EXISTS session_index (
                    project_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    mtime INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    PRIMARY KEY (project_key, session_id)
                );
                CREATE TABLE IF NOT EXISTS session_transcripts (
                    project_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    subpath TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    entry_json TEXT NOT NULL,
                    PRIMARY KEY (project_key, session_id, subpath, sequence)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _append_sync(
        self, key: Mapping[str, str], entries: Sequence[SessionStoreEntry]
    ) -> None:
        project_key, session_id, subpath = self._key_values(key)
        serialized = [
            json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
            for entry in entries
        ]
        mtime = int(time.time_ns() // 1_000_000)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT mtime, summary_json FROM session_index "
                    "WHERE project_key = ? AND session_id = ?",
                    (project_key, session_id),
                ).fetchone()
                if subpath:
                    if row is None:
                        summary_json = "{}"
                        connection.execute(
                            """
                            INSERT INTO session_index
                                (project_key, session_id, mtime, summary_json)
                            VALUES (?, ?, ?, ?)
                            """,
                            (project_key, session_id, mtime, summary_json),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE session_index
                            SET mtime = ?
                            WHERE project_key = ? AND session_id = ?
                            """,
                            (mtime, project_key, session_id),
                        )
                else:
                    previous = None
                    if row is not None:
                        previous = {
                            "session_id": session_id,
                            "mtime": row[0],
                            "data": json.loads(row[1]),
                        }
                    summary = _fold_session_summary(previous, key, list(entries))
                    summary["mtime"] = mtime
                    summary_json = json.dumps(
                        summary["data"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        INSERT INTO session_index
                            (project_key, session_id, mtime, summary_json)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(project_key, session_id) DO UPDATE SET
                            mtime = excluded.mtime,
                            summary_json = excluded.summary_json
                        """,
                        (project_key, session_id, mtime, summary_json),
                    )
                for entry_json in serialized:
                    next_sequence = connection.execute(
                        """
                        SELECT COALESCE(MAX(sequence), -1) + 1
                        FROM session_transcripts
                        WHERE project_key = ? AND session_id = ? AND subpath = ?
                        """,
                        (project_key, session_id, subpath),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO session_transcripts
                            (project_key, session_id, subpath, sequence, entry_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (project_key, session_id, subpath, next_sequence, entry_json),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _load_sync(self, key: Mapping[str, str]) -> list[SessionStoreEntry] | None:
        project_key, session_id, subpath = self._key_values(key)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT entry_json FROM session_transcripts
                WHERE project_key = ? AND session_id = ? AND subpath = ?
                ORDER BY sequence
                """,
                (project_key, session_id, subpath),
            ).fetchall()
        if not rows:
            return None
        return [json.loads(row[0]) for row in rows]

    def _list_sessions_sync(self, project_key: str) -> list[SessionStoreListEntry]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, mtime FROM session_index
                WHERE project_key = ?
                ORDER BY mtime DESC, session_id
                """,
                (project_key,),
            ).fetchall()
        return [{"session_id": row[0], "mtime": row[1]} for row in rows]

    def _list_session_summaries_sync(
        self, project_key: str
    ) -> list[SessionSummaryEntry]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, mtime, summary_json FROM session_index
                WHERE project_key = ?
                ORDER BY mtime DESC, session_id
                """,
                (project_key,),
            ).fetchall()
        return [
            {
                "session_id": row[0],
                "mtime": row[1],
                "data": json.loads(row[2]),
            }
            for row in rows
        ]

    def _delete_sync(self, key: Mapping[str, str]) -> None:
        project_key, session_id, subpath = self._key_values(key)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if subpath:
                    connection.execute(
                        """
                        DELETE FROM session_transcripts
                        WHERE project_key = ? AND session_id = ? AND subpath = ?
                        """,
                        (project_key, session_id, subpath),
                    )
                else:
                    connection.execute(
                        """
                        DELETE FROM session_transcripts
                        WHERE project_key = ? AND session_id = ?
                        """,
                        (project_key, session_id),
                    )
                    connection.execute(
                        """
                        DELETE FROM session_index
                        WHERE project_key = ? AND session_id = ?
                        """,
                        (project_key, session_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _list_subkeys_sync(self, key: Mapping[str, str]) -> list[str]:
        project_key, session_id, _ = self._key_values(key)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT subpath FROM session_transcripts
                WHERE project_key = ? AND session_id = ? AND subpath != ''
                ORDER BY subpath
                """,
                (project_key, session_id),
            ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _key_values(key: Mapping[str, str]) -> tuple[str, str, str]:
        project_key = key.get("project_key", "")
        session_id = key.get("session_id", "")
        if not project_key or not session_id:
            raise ValueError("Session key requires project_key and session_id")
        return project_key, session_id, key.get("subpath", "")
