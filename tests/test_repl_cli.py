import asyncio
import io
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.repl import AgenticRagRepl
from agentic_rag.sessions import SESSION_PROJECT_KEY, SessionNotFoundError


class FakeHealthAdapter:
    def __init__(self):
        self.closed = False

    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)

    async def aclose(self):
        self.closed = True


class FakeCommandClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeRuntime:
    def __init__(self, event_sequences):
        self.event_sequences = event_sequences
        self.calls = []
        self.session_stores = []

    async def stream(self, *, session_id, message, target_uri, session_store=None):
        if session_store is None:
            raise AssertionError("Application must pass its session store")
        self.calls.append((session_id, message))
        self.session_stores.append(session_store)
        for event in self.event_sequences.pop(0):
            yield event


class CloseTrackingApplication(AgenticRagApplication):
    def __init__(self, runtime, database_path):
        settings = Settings.from_env(
            environ={
                "DEEPSEEK_API_KEY": "model-secret",
                "SESSION_DATABASE_PATH": str(database_path),
            },
            env_file=Path("does-not-exist.env"),
        )
        self.health_adapter = FakeHealthAdapter()
        self.command_client = FakeCommandClient()
        super().__init__(
            settings=settings,
            health_adapter=self.health_adapter,
            command_client=self.command_client,
            claude_runtime=runtime,
        )
        self.shutdown_called = False

    async def shutdown_jobs(self):
        self.shutdown_called = True

    async def get_session(self, session_id):
        if session_id not in {"11111111-1111-1111-1111-111111111111"}:
            raise SessionNotFoundError(session_id)
        return {"session_id": session_id, "entries": []}


class PausingApplication:
    def __init__(self):
        self.health_adapter = None
        self.command_client = None
        self.started = asyncio.Event()
        self.stream_closed = False
        self.calls = []

    async def stream_conversation(self, *, session_id, message, target_uri=None):
        selected = session_id or str(uuid.uuid4())
        self.calls.append((selected, message))
        self.started.set()
        try:
            yield {"type": "message_start", "session_id": selected}
            await asyncio.sleep(30)
            yield {"type": "done", "session_id": selected, "citations": []}
        finally:
            self.stream_closed = True

    async def get_session(self, session_id):
        raise AssertionError("not used")

    async def shutdown_jobs(self):
        self.jobs_shutdown = True


def make_application(events, directory):
    runtime = FakeRuntime(events)
    return runtime, CloseTrackingApplication(runtime, Path(directory) / "sessions.sqlite3")


def run_repl(application, inputs, *, interrupt=False):
    output = io.StringIO()
    queue = list(inputs)

    def read_prompt():
        if interrupt:
            raise KeyboardInterrupt
        if not queue:
            raise AssertionError("No prompt supplied for this test")
        return queue.pop(0)

    repl = AgenticRagRepl(application, input=read_prompt, output=output)
    return repl, output


class ReplRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_rendering_separates_thinking_tools_answer_and_citations(self):
        with TemporaryDirectory() as directory:
            runtime, application = make_application(
                [
                    [
                        {"type": "thinking_delta", "text": "先定位证据"},
                        {
                            "type": "tool_call",
                            "tool_call_id": "call-1",
                            "tool_name": "find",
                            "arguments": {"query": "OpenViking"},
                        },
                        {
                            "type": "tool_result",
                            "tool_call_id": "call-1",
                            "tool_name": "find",
                            "error": False,
                            "content": "viking://Demo/A viking://Demo/B",
                        },
                        {"type": "text_delta", "text": "答案"},
                        {"type": "text_delta", "text": "第一段"},
                        {"type": "citation", "citation": "viking://Demo/A"},
                        {
                            "type": "done",
                            "citations": [
                                "viking://Demo/A",
                                "viking://Demo/A",
                                "viking://Demo/B",
                            ],
                        },
                    ]
                ],
                directory,
            )
            repl, output = run_repl(application, ["知识库里有什么？", "/exit"])
            await repl.run_async()

        text = output.getvalue()
        self.assertIn("[检索] 正在查询 OpenViking...", text)
        self.assertIn("[思考] 先定位证据", text)
        self.assertIn("回答：答案第一段", text)
        self.assertIn("引用：", text)
        self.assertIn("- viking://Demo/A", text)
        self.assertIn("- viking://Demo/B", text)
        self.assertEqual(text.count("- viking://Demo/A"), 1)
        self.assertLess(text.index("[思考] 先定位证据"), text.index("回答："))
        self.assertEqual(text.count("[思考]"), 1)

    async def test_follow_up_uses_the_generated_session_and_application_store(self):
        with TemporaryDirectory() as directory:
            runtime, application = make_application(
                [
                    [{"type": "message_start"}, {"type": "done", "citations": []}],
                    [{"type": "message_start"}, {"type": "done", "citations": []}],
                ],
                directory,
            )
            repl, output = run_repl(application, ["第一问", "第二问", "/exit"])
            await repl.run_async()

            self.assertEqual(
                [call[1] for call in runtime.calls], ["第一问", "第二问"]
            )
            self.assertEqual(runtime.calls[0][0], runtime.calls[1][0])
            uuid.UUID(runtime.calls[0][0])
            self.assertIs(runtime.session_stores[0], application.session_store)
            self.assertIs(runtime.session_stores[1], application.session_store)
            self.assertIn("会话已创建", output.getvalue())


class ReplSessionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_current_session_switch_clear_and_exit(self):
        with TemporaryDirectory() as directory:
            _, application = make_application([], directory)
            repl, output = run_repl(
                application,
                [
                    "/new",
                    "/current",
                    "/session 11111111-1111-1111-1111-111111111111",
                    "/current",
                    "/clear",
                    "/current",
                    "/exit",
                ],
            )
            await repl.run_async()

        text = output.getvalue()
        self.assertIn("已开始新会话：", text)
        self.assertIn("11111111-1111-1111-1111-111111111111", text)
        self.assertIn("已切换到会话：11111111-1111-1111-1111-111111111111", text)
        self.assertIn("\x1b[2J\x1b[H", text)
        self.assertGreaterEqual(text.count("\u5f53\u524d\u4f1a\u8bdd"), 2)
        self.assertTrue(application.shutdown_called)
        self.assertTrue(application.health_adapter.closed)
        self.assertTrue(application.command_client.closed)

    async def test_session_rejects_invalid_or_missing_sessions(self):
        with TemporaryDirectory() as directory:
            _, application = make_application([], directory)
            repl, output = run_repl(
                application,
                [
                    "/session not-a-uuid",
                    "/session 22222222-2222-2222-2222-222222222222",
                    "/exit",
                ],
            )
            await repl.run_async()

        text = output.getvalue()
        self.assertIn("会话标识必须是 UUID", text)
        self.assertIn("会话不存在：22222222-2222-2222-2222-222222222222", text)


class ReplShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_interrupt_uses_clean_shutdown(self):
        with TemporaryDirectory() as directory:
            _, application = make_application([], directory)
            repl, output = run_repl(application, [], interrupt=True)
            await repl.run_async()

        self.assertTrue(application.shutdown_called)
        self.assertTrue(application.health_adapter.closed)
        self.assertTrue(application.command_client.closed)

    async def test_interrupting_active_stream_closes_stream_and_flushes_state(self):
        application = PausingApplication()
        output = io.StringIO()
        prompts = iter(["当前问题", "/exit"])
        repl = AgenticRagRepl(application, input=lambda: next(prompts), output=output)
        run_task = asyncio.create_task(repl.run_async())
        await application.started.wait()
        await repl.shutdown()
        await run_task

        self.assertTrue(application.stream_closed)
        self.assertTrue(application.jobs_shutdown)
        self.assertIn("已中断当前回答", output.getvalue())
        self.assertIn("已退出 Agentic RAG REPL", output.getvalue())


    async def test_interrupting_active_stream_leaves_sqlite_session_usable(self):
        class PausingPersistentRuntime:
            def __init__(self):
                self.produced = asyncio.Event()
                self.calls = []

            async def stream(self, *, session_id, message, target_uri, session_store=None):
                self.calls.append((session_id, message, session_store))
                key = {"project_key": SESSION_PROJECT_KEY, "session_id": session_id}
                await session_store.append(
                    key,
                    [{"type": "user", "message": {"role": "user", "content": message}}],
                )
                yield {"type": "message_start", "session_id": session_id}
                yield {"type": "text_delta", "text": "partial answer"}
                self.produced.set()
                await asyncio.sleep(30)

        with TemporaryDirectory() as directory:
            runtime = PausingPersistentRuntime()
            application = CloseTrackingApplication(
                runtime, Path(directory) / "sessions.sqlite3"
            )
            output = io.StringIO()
            prompts = iter(["interrupted-question", "/exit"])
            repl = AgenticRagRepl(
                application, input=lambda: next(prompts), output=output
            )
            run_task = asyncio.create_task(repl.run_async())
            await runtime.produced.wait()
            await repl.shutdown()
            await run_task

            session_id = runtime.calls[0][0]
            entries = await application.session_store.load(
                {"project_key": SESSION_PROJECT_KEY, "session_id": session_id}
            )
            self.assertEqual(
                entries,
                [
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "interrupted-question"},
                    }
                ],
            )
            self.assertTrue(application.shutdown_called)
            self.assertIn("\u5df2\u4e2d\u65ad", output.getvalue())


if __name__ == "__main__":
    unittest.main()
