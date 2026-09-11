import asyncio
import unittest
import uuid
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import httpx

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.service import create_app
from agentic_rag.sessions import (
    SESSION_PROJECT_KEY,
    SessionNotFoundError,
    SqliteSessionStore,
)


class FakeHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class PersistentFakeRuntime:
    def __init__(self):
        self.calls = []
        self.prior_contexts = []
        self.session_stores = []

    async def stream(
        self,
        *,
        session_id,
        message,
        target_uri,
        session_store=None,
    ):
        if session_store is None:
            raise AssertionError("Application must pass its session store")
        self.session_stores.append(session_store)
        key = {
            "project_key": SESSION_PROJECT_KEY,
            "session_id": session_id,
        }
        prior_entries = await session_store.load(key) or []
        prior_messages = [
            entry["message"]["content"]
            for entry in prior_entries
            if entry.get("type") == "user"
        ]
        self.prior_contexts.append(prior_messages)
        self.calls.append(message)
        await session_store.append(
            key,
            [
                {
                    "type": "user",
                    "message": {"role": "user", "content": message},
                }
            ],
        )
        await session_store.append(
            {**key, "subpath": "subagents/agent-1"},
            [{"type": "subagent", "content": "子代理记录"}],
        )
        yield {"type": "text_delta", "text": f"收到：{message}"}
        await session_store.append(
            key,
            [
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": f"收到：{message}"},
                }
            ],
        )


class SerializingRuntime(PersistentFakeRuntime):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def stream(
        self,
        *,
        session_id,
        message,
        target_uri,
        session_store=None,
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            async for event in super().stream(
                session_id=session_id,
                message=message,
                target_uri=target_uri,
                session_store=session_store,
            ):
                yield event
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1


def make_application(runtime, database_path):
    settings = Settings.from_env(
        environ={
            "DEEPSEEK_API_KEY": "model-secret",
            "SESSION_DATABASE_PATH": str(database_path),
        },
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeHealthAdapter(),
        claude_runtime=runtime,
    )


class PausingRuntime(PersistentFakeRuntime):
    def __init__(self):
        super().__init__()
        self.produced = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        *,
        session_id,
        message,
        target_uri,
        session_store=None,
    ):
        async for event in super().stream(
            session_id=session_id,
            message=message,
            target_uri=target_uri,
            session_store=session_store,
        ):
            yield event
            self.produced.set()
            await self.release.wait()


async def collect(application, **kwargs):
    return [event async for event in application.stream_conversation(**kwargs)]


class SqliteSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_list_load_subkey_and_delete_keep_entries_opaque(self):
        with TemporaryDirectory() as directory:
            store = SqliteSessionStore(Path(directory) / "sessions.sqlite3")
            main_key = {
                "project_key": SESSION_PROJECT_KEY,
                "session_id": "session-store",
            }
            subkey = {**main_key, "subpath": "subagents/agent-1"}

            entry = {
                "type": "user",
                "timestamp": "2026-09-10T12:00:00Z",
                "cwd": "D:\\project\\Harness\\python-claude-sdk",
                "isSidechain": False,
                "customTitle": "会话标题",
                "lastPrompt": "知识库里有什么？",
                "message": {
                    "role": "user",
                    "content": "知识库里有什么？",
                },
            }
            await store.append(main_key, [entry])
            await store.append(subkey, [{"type": "subagent", "opaque": ["记录"]}])
            mtime_before = (await store.list_session_summaries(SESSION_PROJECT_KEY))[0][
                "mtime"
            ]
            await asyncio.sleep(0.01)
            await store.append(subkey, [{"type": "subagent", "opaque": ["更新"]}])
            mtime_after = (await store.list_session_summaries(SESSION_PROJECT_KEY))[0][
                "mtime"
            ]
            self.assertGreater(mtime_after, mtime_before)

            summaries = await store.list_session_summaries(SESSION_PROJECT_KEY)
            self.assertEqual(
                [
                    (
                        item["session_id"],
                        item["data"]["first_prompt"],
                        item["data"]["custom_title"],
                        item["data"]["last_prompt"],
                    )
                    for item in summaries
                ],
                [("session-store", "知识库里有什么？", "会话标题", "知识库里有什么？")],
            )
            self.assertEqual(await store.load(main_key), [entry])
            self.assertEqual(
                await store.load(subkey),
                [
                    {"type": "subagent", "opaque": ["记录"]},
                    {"type": "subagent", "opaque": ["更新"]},
                ],
            )
            self.assertEqual(await store.list_subkeys(main_key), ["subagents/agent-1"])

            await store.delete(main_key)
            self.assertIsNone(await store.load(main_key))
            self.assertIsNone(await store.load(subkey))
            self.assertEqual(await store.list_session_summaries(SESSION_PROJECT_KEY), [])

            connection = sqlite3.connect(store.path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
            finally:
                connection.close()


class PersistentSessionApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_session_restores_context_and_subkeys_after_restart(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            first_runtime = PersistentFakeRuntime()
            application = make_application(first_runtime, database_path)

            first_events = await collect(
                application, session_id=None, message="知识库里有什么？"
            )
            session_id = first_events[0]["session_id"]
            uuid.UUID(session_id)
            self.assertEqual(first_runtime.prior_contexts, [[]])

            restarted_runtime = PersistentFakeRuntime()
            restarted_application = make_application(restarted_runtime, database_path)
            second_events = await collect(
                restarted_application,
                session_id=session_id,
                message="它的第一条内容是什么？",
            )

            self.assertEqual(second_events[-1]["session_id"], session_id)
            self.assertEqual(restarted_runtime.prior_contexts, [["知识库里有什么？"]])
            self.assertIsNot(
                first_runtime.session_stores[0], restarted_runtime.session_stores[0]
            )
            detail = await restarted_application.get_session(session_id)
            self.assertEqual(detail["subkeys"], ["subagents/agent-1"])

    async def test_concurrent_writes_to_one_session_are_serialized_in_order(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            runtime = SerializingRuntime()
            application = make_application(runtime, database_path)

            await asyncio.gather(
                collect(application, session_id="same-session", message="第一问"),
                collect(application, session_id="same-session", message="第二问"),
            )

            self.assertEqual(runtime.max_active, 1)
            detail = await application.get_session("same-session")
            user_messages = [
                entry["message"]["content"]
                for entry in detail["entries"]
                if entry.get("type") == "user"
            ]
            self.assertEqual(user_messages, ["第一问", "第二问"])

    async def test_delete_waits_for_an_active_stream_and_removes_its_data(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            runtime = PausingRuntime()
            application = make_application(runtime, database_path)

            stream_task = asyncio.create_task(
                collect(application, session_id="deleting-session", message="第一问")
            )
            await runtime.produced.wait()
            delete_task = asyncio.create_task(
                application.delete_session("deleting-session")
            )
            await asyncio.sleep(0.01)
            self.assertFalse(delete_task.done())

            runtime.release.set()
            events = await stream_task
            deleted = await delete_task

            self.assertEqual(events[-1]["type"], "done")
            self.assertEqual(deleted, {"deleted": True, "session_id": "deleting-session"})
            self.assertEqual(await application.list_sessions(), {"sessions": []})

    async def test_delete_cannot_interleave_with_an_inflight_inspection(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            application = make_application(PersistentFakeRuntime(), database_path)
            await collect(application, session_id="race-session", message="第一问")

            store = application.session_store
            original_summaries = store.list_session_summaries
            summaries_seen = asyncio.Event()
            proceed = asyncio.Event()
            summary_calls = 0

            async def pausing_summaries(project_key):
                nonlocal summary_calls
                summary_calls += 1
                result = await original_summaries(project_key)
                if summary_calls == 1:
                    summaries_seen.set()
                    await proceed.wait()
                return result

            store.list_session_summaries = pausing_summaries

            get_task = asyncio.create_task(application.get_session("race-session"))
            await summaries_seen.wait()
            delete_task = asyncio.create_task(application.delete_session("race-session"))
            await asyncio.sleep(0.02)
            self.assertFalse(delete_task.done())

            proceed.set()
            detail = await get_task
            deleted = await delete_task

            self.assertTrue(detail["entries"])
            self.assertEqual(deleted, {"deleted": True, "session_id": "race-session"})
            with self.assertRaises(SessionNotFoundError):
                await application.get_session("race-session")

    async def test_session_metadata_and_transcripts_are_redacted(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            store = SqliteSessionStore(database_path)
            await store.append(
                {
                    "project_key": SESSION_PROJECT_KEY,
                    "session_id": "session-secret",
                },
                [
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": "凭据是 model-secret",
                        },
                    }
                ],
            )
            application = make_application(PersistentFakeRuntime(), database_path)

            listing = await application.list_sessions()
            detail = await application.get_session("session-secret")
            self.assertNotIn("model-secret", str(listing))
            self.assertNotIn("model-secret", str(detail))
            self.assertIn("[redacted]", str(detail))

    async def test_rest_sessions_can_be_listed_inspected_and_deleted(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "sessions.sqlite3"
            runtime = PersistentFakeRuntime()
            application = make_application(runtime, database_path)
            events = await collect(
                application, session_id="session-rest", message="知识库里有什么？"
            )
            self.assertEqual(events[-1]["session_id"], "session-rest")

            transport = httpx.ASGITransport(app=create_app(application=application))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                listed = await client.get("/sessions")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(
                    [item["session_id"] for item in listed.json()["sessions"]],
                    ["session-rest"],
                )

                inspected = await client.get("/sessions/session-rest")
                self.assertEqual(inspected.status_code, 200)
                body = inspected.json()
                self.assertEqual(body["session_id"], "session-rest")
                self.assertEqual(body["subkeys"], ["subagents/agent-1"])

                deleted = await client.delete("/sessions/session-rest")
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(
                    deleted.json(), {"deleted": True, "session_id": "session-rest"}
                )

                missing = await client.get("/sessions/session-rest")
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(missing.json()["error"]["code"], "SESSION_NOT_FOUND")

            self.assertEqual(await application.list_sessions(), {"sessions": []})
            with self.assertRaises(SessionNotFoundError):
                await application.get_session("session-rest")


if __name__ == "__main__":
    unittest.main()
