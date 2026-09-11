import unittest
from datetime import datetime
from pathlib import Path

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus


class FakeHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeClaudeRuntime:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def stream(self, *, session_id, message, target_uri, session_store=None):
        self.calls.append(
            {
                "session_id": session_id,
                "message": message,
                "target_uri": target_uri,
            }
        )
        for event in self.events:
            yield event


def make_application(runtime, environ=None):
    settings = Settings.from_env(
        environ={"DEEPSEEK_API_KEY": "model-secret", **(environ or {})},
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeHealthAdapter(),
        claude_runtime=runtime,
    )


class AgentEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, application, **kwargs):
        stream = application.stream_conversation(**kwargs)
        return [event async for event in stream]

    async def test_successful_lifecycle_separates_thinking_text_tools_and_citations(self):
        runtime = FakeClaudeRuntime(
            [
                {"type": "thinking_delta", "text": "先检索"},
                {
                    "type": "tool_call",
                    "tool_call_id": "call-1",
                    "tool_name": "find",
                    "arguments": {"query": "知识库", "target_uri": "viking://demo"},
                },
                {
                    "type": "tool_result",
                    "tool_call_id": "call-1",
                    "tool_name": "find",
                    "error": False,
                    "content": {"items": [{"uri": "VIKING://Demo/A?x=1"}]},
                },
                {
                    "type": "text_delta",
                    "text": "答案来自 viking://Demo/A?x=1。",
                },
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-1",
            message="知识库里有什么？",
            target_uri="viking://demo",
        )

        self.assertEqual(
            [event["type"] for event in events],
            [
                "message_start",
                "thinking_delta",
                "tool_call",
                "tool_result",
                "citation",
                "text_delta",
                "done",
            ],
        )
        self.assertEqual(
            runtime.calls,
            [
                {
                    "session_id": "session-1",
                    "message": "知识库里有什么？",
                    "target_uri": "viking://demo",
                }
            ],
        )
        request_id = events[0]["request_id"]
        for event in events:
            with self.subTest(event_type=event["type"]):
                self.assertEqual(event["request_id"], request_id)
                self.assertEqual(event["session_id"], "session-1")
                datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))

        self.assertEqual(events[1]["text"], "先检索")
        self.assertEqual(
            events[2],
            {
                "type": "tool_call",
                "request_id": request_id,
                "session_id": "session-1",
                "timestamp": events[2]["timestamp"],
                "tool_call_id": "call-1",
                "tool_name": "find",
                "arguments": {
                    "query": "知识库",
                    "target_uri": "viking://demo",
                },
            },
        )
        self.assertEqual(events[3]["error"], False)
        self.assertEqual(events[3]["content"], {"items": [{"uri": "VIKING://Demo/A?x=1"}]})
        self.assertEqual(events[4]["citation"], "viking://Demo/A?x=1")
        self.assertEqual(events[5]["text"], "答案来自 viking://Demo/A?x=1。")
        self.assertEqual(events[6]["citations"], ["viking://Demo/A?x=1"])
    async def test_citation_split_across_text_deltas_is_extracted_from_final_text(self):
        runtime = FakeClaudeRuntime(
            [
                {"type": "text_delta", "text": "答案来自 viking://"},
                {"type": "text_delta", "text": "Demo/A。"},
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-split",
            message="知识库里有什么？",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["message_start", "text_delta", "text_delta", "citation", "done"],
        )
        self.assertEqual(events[-2]["citation"], "viking://Demo/A")
        self.assertEqual(events[-1]["citations"], ["viking://Demo/A"])

    async def test_citations_from_markdown_and_escaped_newline_text_are_normalized(self):
        runtime = FakeClaudeRuntime(
            [
                {
                    "type": "text_delta",
                    "text": (
                        "来源 [viking://Demo/A](viking://Demo/A)，"
                        "工具结果 viking://Demo/B\\n。"
                    ),
                }
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-citation-format",
            message="知识库里有什么？",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["message_start", "text_delta", "citation", "citation", "done"],
        )
        self.assertEqual(
            events[-1]["citations"],
            ["viking://Demo/A", "viking://Demo/B"],
        )

    async def test_non_uri_scheme_mention_does_not_break_citation_extraction(self):
        runtime = FakeClaudeRuntime(
            [
                {
                    "type": "tool_result",
                    "tool_call_id": "call-scheme",
                    "tool_name": "find",
                    "error": False,
                    "content": (
                        "?????? viking://???????? "
                        "viking://resources/agentic-rag-ticket08/source.md?"
                    ),
                },
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-scheme",
            message="????????",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["message_start", "tool_result", "citation", "done"],
        )
        self.assertEqual(
            events[-1]["citations"],
            ["viking://resources/agentic-rag-ticket08/source.md"],
        )

    async def test_tool_error_remains_an_observed_result_and_can_still_complete(self):
        runtime = FakeClaudeRuntime(
            [
                {
                    "type": "tool_call",
                    "tool_call_id": "call-error",
                    "tool_name": "read",
                    "arguments": {"uri": "viking://demo/missing"},
                },
                {
                    "type": "tool_result",
                    "tool_call_id": "call-error",
                    "tool_name": "read",
                    "error": True,
                    "content": "Resource not found",
                },
                {"type": "text_delta", "text": "知识库中没有找到支持该问题的资料。"},
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-tool-error",
            message="缺失事实是什么？",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["message_start", "tool_call", "tool_result", "text_delta", "done"],
        )
        self.assertTrue(events[2]["error"])
        self.assertEqual(events[2]["content"], "Resource not found")
        self.assertEqual(events[-1]["citations"], [])

    async def test_terminal_runtime_failure_emits_final_error_without_secret(self):
        runtime = FakeClaudeRuntime(
            [
                {"type": "thinking_delta", "text": "开始检索"},
                {
                    "type": "error",
                    "code": "MODEL_OVERLOADED",
                    "message": "provider failed after model-secret",
                },
            ]
        )
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-terminal",
            message="知识库里有什么？",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["message_start", "thinking_delta", "error"],
        )
        self.assertEqual(events[-1]["code"], "MODEL_OVERLOADED")
        self.assertEqual(events[-1]["message"], "Agent runtime failed")
        self.assertNotIn("model-secret", str(events))

    async def test_terminal_runtime_failure_closes_the_underlying_stream(self):
        class ClosingRuntime:
            def __init__(self):
                self.closed = False

            async def stream(self, *, session_id, message, target_uri, session_store=None):
                try:
                    yield {"type": "error", "code": "MODEL_OVERLOADED", "message": "failed"}
                    yield {"type": "text_delta", "text": "must not render"}
                finally:
                    self.closed = True

        runtime = ClosingRuntime()
        application = make_application(runtime)

        events = await self.collect(
            application,
            session_id="session-close-terminal",
            message="????????",
        )

        self.assertEqual([event["type"] for event in events], ["message_start", "error"])
        self.assertTrue(runtime.closed)

    async def test_exception_after_terminal_error_does_not_emit_a_second_error(self):
        class ErrorThenRaiseRuntime:
            async def stream(self, *, session_id, message, target_uri, session_store=None):
                yield {"type": "error", "code": "MODEL_OVERLOADED", "message": "failed"}
                raise RuntimeError("cleanup failed")

        application = make_application(ErrorThenRaiseRuntime())

        events = await self.collect(
            application,
            session_id="session-terminal-cleanup",
            message="????????",
        )

        self.assertEqual(
            [event["type"] for event in events], ["message_start", "error"]
        )
        self.assertEqual(events[-1]["code"], "MODEL_OVERLOADED")


if __name__ == "__main__":
    unittest.main()
