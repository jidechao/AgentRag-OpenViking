import json
import unittest
from pathlib import Path

import httpx

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.service import create_app


class FakeHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeClaudeRuntime:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def stream(self, *, session_id, message, target_uri, session_store=None):
        self.calls.append((session_id, message, target_uri))
        for event in self.events:
            yield event


def make_application(runtime):
    settings = Settings.from_env(
        environ={"DEEPSEEK_API_KEY": "model-secret"},
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeHealthAdapter(),
        claude_runtime=runtime,
    )


def parse_sse(response_text):
    parsed = []
    for block in response_text.strip().replace("\r\n", "\n").split("\n\n"):
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if event_name is not None:
            parsed.append((event_name, json.loads("\n".join(data_lines))))
    return parsed


class StreamingQaEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, application, body):
        transport = httpx.ASGITransport(app=create_app(application=application))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/qa/stream", json=body)

    async def test_sse_event_names_match_payload_types_and_forwards_target_uri(self):
        runtime = FakeClaudeRuntime(
            [
                {"type": "thinking_delta", "text": "检索中"},
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
                    "content": "viking://Demo/A",
                },
                {"type": "text_delta", "text": "答案 viking://Demo/A。"},
            ]
        )
        response = await self.request(
            make_application(runtime),
            {
                "session_id": "session-rest",
                "message": "知识库里有什么？",
                "target_uri": "viking://demo",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = parse_sse(response.text)
        self.assertEqual(
            [(event_name, payload["type"]) for event_name, payload in events],
            [
                ("message_start", "message_start"),
                ("thinking_delta", "thinking_delta"),
                ("tool_call", "tool_call"),
                ("tool_result", "tool_result"),
                ("citation", "citation"),
                ("text_delta", "text_delta"),
                ("done", "done"),
            ],
        )
        self.assertEqual(runtime.calls, [("session-rest", "知识库里有什么？", "viking://demo")])
        self.assertEqual(events[-1][1]["citations"], ["viking://Demo/A"])

    async def test_terminal_failure_is_returned_as_a_final_sse_error_event(self):
        runtime = FakeClaudeRuntime(
            [{"type": "error", "code": "MODEL_OVERLOADED", "message": "provider unavailable"}]
        )
        response = await self.request(
            make_application(runtime),
            {"message": "知识库里有什么？"},
        )

        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "message_start")
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "MODEL_OVERLOADED")
        self.assertTrue(events[-1][1]["request_id"])
        self.assertTrue(events[-1][1]["session_id"])

    async def test_invalid_qa_request_returns_the_error_through_sse(self):
        response = await self.request(
            make_application(FakeClaudeRuntime([])),
            {"session_id": "session-invalid", "message": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = parse_sse(response.text)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "error")
        self.assertEqual(events[0][1]["type"], "error")
        self.assertEqual(events[0][1]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            events[0][1]["message"],
            "QA request body does not match the streaming contract",
        )
        self.assertEqual(events[0][1]["session_id"], "session-invalid")
        self.assertTrue(events[0][1]["request_id"])
        self.assertTrue(events[0][1]["timestamp"])


if __name__ == "__main__":
    unittest.main()
