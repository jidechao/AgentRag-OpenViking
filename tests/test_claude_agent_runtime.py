import unittest
from pathlib import Path
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agentic_rag.agent import (
    EXPECTED_OPENVIKING_MCP_TOOLS,
    ClaudeAgentRuntime,
    OpenVikingMcpDriftError,
    validate_mcp_inventory,
)
from agentic_rag.config import Settings


class FakeQuery:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def __call__(self, *, prompt, options, transport=None):
        self.calls.append({"prompt": prompt, "options": options})
        for message in self.messages:
            yield message


class FakeSessionStore:
    def __init__(self, entries=None):
        self.entries = entries
        self.load_calls = []

    async def load(self, key):
        self.load_calls.append(dict(key))
        return self.entries


class FakeMcpInventory:
    def __init__(self, names):
        self.names = names
        self.calls = 0

    async def tool_names(self):
        self.calls += 1
        return list(self.names)


def make_settings(**overrides):
    values = {
        "DEEPSEEK_API_KEY": "model-secret",
        "OPENVIKING_BASE_URL": "http://127.0.0.1:1933",
        **overrides,
    }
    return Settings.from_env(
        environ=values,
        env_file=Path("does-not-exist.env"),
    )


def make_runtime(query, inventory=None, settings=None):
    return ClaudeAgentRuntime(
        settings=settings or make_settings(),
        query=query,
        mcp_inventory=inventory or FakeMcpInventory(EXPECTED_OPENVIKING_MCP_TOOLS),
    )


async def collect(runtime, **kwargs):
    kwargs.setdefault("session_store", FakeSessionStore())
    return [event async for event in runtime.stream(**kwargs)]


class ClaudeAgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_options_use_native_mcp_deepseek_thinking_and_streaming(self):
        session_id = str(uuid.uuid4())
        query = FakeQuery(
            [
                StreamEvent(
                    uuid="stream-1",
                    session_id=session_id,
                    event={
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "thinking_delta", "thinking": "先定位来源"},
                    },
                ),
                StreamEvent(
                    uuid="stream-1",
                    session_id=session_id,
                    event={
                        "type": "content_block_start",
                        "index": 1,
                        "content_block": {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "mcp__openviking__find",
                            "input": {},
                        },
                    },
                ),
                StreamEvent(
                    uuid="stream-1",
                    session_id=session_id,
                    event={
                        "type": "content_block_delta",
                        "index": 1,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": "{\"query\":\"OpenViking\"}",
                        },
                    },
                ),
                StreamEvent(
                    uuid="stream-1",
                    session_id=session_id,
                    event={"type": "content_block_stop", "index": 1},
                ),
                UserMessage(
                    content=[
                        ToolResultBlock(
                            tool_use_id="tool-1",
                            content="viking://Demo/A",
                            is_error=False,
                        )
                    ]
                ),
                StreamEvent(
                    uuid="stream-2",
                    session_id=session_id,
                    event={
                        "type": "content_block_delta",
                        "index": 2,
                        "delta": {"type": "text_delta", "text": "答案 viking://Demo/A"},
                    },
                ),
                ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="答案 viking://Demo/A",
                ),
            ]
        )
        runtime = make_runtime(query)

        events = await collect(
            runtime,
            session_id=session_id,
            message="知识库里有什么？",
            target_uri="viking://demo",
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["thinking_delta", "tool_call", "tool_result", "text_delta"],
        )
        self.assertEqual(events[1]["tool_name"], "find")
        self.assertEqual(events[1]["arguments"], {"query": "OpenViking"})
        self.assertEqual(events[2]["tool_name"], "find")
        self.assertEqual(events[2]["error"], False)
        self.assertIn("viking://Demo/A", events[3]["text"])

        call = query.calls[0]
        options = call["options"]
        self.assertEqual(options.model, "deepseek-flash[1m]")
        self.assertEqual(options.thinking, {"type": "enabled", "budget_tokens": 5000})
        self.assertTrue(options.include_partial_messages)
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.setting_sources, [])
        self.assertEqual(options.tools, [])
        self.assertEqual(
            options.mcp_servers,
            {
                "openviking": {
                    "type": "http",
                    "url": "http://127.0.0.1:1933/mcp",
                }
            },
        )
        self.assertEqual(
            options.env["ANTHROPIC_BASE_URL"],
            "https://api.deepseek.com/anthropic",
        )
        self.assertEqual(options.env["ANTHROPIC_AUTH_TOKEN"], "model-secret")
        self.assertEqual(options.env["ANTHROPIC_MODEL"], "deepseek-flash[1m]")
        self.assertIn("viking://demo", call["prompt"])
        self.assertIn("知识库里有什么？", call["prompt"])
        self.assertIn("viking://", options.system_prompt)
        self.assertIn("refuse", options.system_prompt)
        self.assertIn("without viking:// citations", options.system_prompt)

    async def test_authenticated_mode_adds_only_the_openviking_auth_header(self):
        query = FakeQuery([])
        runtime = make_runtime(
            query,
            settings=make_settings(OPENVIKING_API_KEY="openviking-secret"),
        )

        await collect(
            runtime,
            session_id=str(uuid.uuid4()),
            message="问题",
            target_uri=None,
        )

        server = query.calls[0]["options"].mcp_servers["openviking"]
        self.assertEqual(server["headers"], {"X-API-Key": "openviking-secret"})

    async def test_persistent_session_resumes_from_the_application_owned_store(self):
        session_id = str(uuid.uuid4())
        session_store = FakeSessionStore([{"type": "user", "message": {"role": "user"}}])
        query = FakeQuery([])
        runtime = make_runtime(query)

        await collect(runtime, session_id=session_id, message="继续", target_uri=None, session_store=session_store)

        self.assertEqual(
            session_store.load_calls,
            [{"project_key": runtime.project_key, "session_id": session_id}],
        )
        self.assertEqual(query.calls[0]["options"].resume, session_id)
        self.assertIsNone(query.calls[0]["options"].session_id)
        self.assertIs(query.calls[0]["options"].session_store, session_store)

    async def test_first_turn_uses_a_stable_uuid_session_id(self):
        session_id = str(uuid.uuid4())
        query = FakeQuery([])
        runtime = make_runtime(query)

        await collect(runtime, session_id=session_id, message="第一轮", target_uri=None)

        self.assertEqual(query.calls[0]["options"].session_id, session_id)
        self.assertIsNone(query.calls[0]["options"].resume)


    async def test_final_sdk_blocks_stream_when_partial_updates_are_unavailable(self):
        session_id = str(uuid.uuid4())
        query = FakeQuery(
            [
                AssistantMessage(
                    content=[
                        ThinkingBlock(thinking="search first", signature="signature"),
                        ToolUseBlock(
                            id="tool-final",
                            name="mcp__openviking__search",
                            input={"query": "证据"},
                        ),
                        TextBlock(text="answer viking://Demo/B"),
                    ],
                    model="deepseek-flash",
                ),
                UserMessage(
                    content=[
                        ToolResultBlock(
                            tool_use_id="tool-final",
                            content="viking://Demo/B",
                            is_error=False,
                        )
                    ]
                ),
                ResultMessage(
                    subtype="success",
                    duration_ms=1,
                    duration_api_ms=1,
                    is_error=False,
                    num_turns=1,
                    session_id=session_id,
                    result="答案 viking://Demo/B",
                ),
            ]
        )
        runtime = make_runtime(query)

        events = await collect(
            runtime,
            session_id=session_id,
            message="question",
            target_uri=None,
        )

        self.assertEqual(
            [event["type"] for event in events],
            ["thinking_delta", "tool_call", "text_delta", "tool_result"],
        )
        self.assertEqual(events[0]["text"], "search first")
        self.assertEqual(events[1]["tool_name"], "search")
        self.assertIn("viking://Demo/B", events[2]["text"])

class OpenVikingMcpInventoryValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_fifteen_tool_inventory_passes_and_is_queried_dynamically(self):
        inventory = FakeMcpInventory(EXPECTED_OPENVIKING_MCP_TOOLS)
        runtime = make_runtime(FakeQuery([]), inventory=inventory)

        report = await runtime.validate_mcp_inventory()

        self.assertEqual(
            report,
            {"tool_count": 15, "missing_tools": [], "unexpected_tools": []},
        )
        self.assertEqual(inventory.calls, 1)

    async def test_material_drift_reports_missing_and_unexpected_tools(self):
        names = [
            "find",
            "search",
            "read",
            "list",
            "tree",
            "remember",
            "write",
            "edit",
            "add_resource",
            "list_watches",
            "cancel_watch",
            "grep",
            "glob",
            "forget",
            "surprise",
        ]

        with self.assertRaises(OpenVikingMcpDriftError) as raised:
            validate_mcp_inventory(names)

        message = str(raised.exception)
        self.assertIn("OpenViking MCP tool inventory drift", message)
        self.assertIn("missing: health", message)
        self.assertIn("unexpected: surprise", message)
        self.assertIn("expected 15 tools, found 15", message)









class FakeHealthAdapter:
    async def check(self):
        from agentic_rag.health import DependencyHealth, DependencyStatus

        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeOpenApiClient:
    def __init__(self):
        self.openapi_calls = 0

    async def get_openapi(self):
        self.openapi_calls += 1
        return {}


def make_service_application(runtime):
    from agentic_rag.application import AgenticRagApplication

    return AgenticRagApplication(
        settings=runtime.settings,
        health_adapter=FakeHealthAdapter(),
        command_client=FakeOpenApiClient(),
        claude_runtime=runtime,
    )


class RuntimeStartupValidationTests(unittest.IsolatedAsyncioTestCase):
    async def with_lifespan(self, runtime):
        from agentic_rag.service import create_app

        application = make_service_application(runtime)
        application.validate_command_surface = lambda document: {"warnings": []}
        return create_app(application=application)

    async def test_service_startup_validates_runtime_mcp_inventory(self):
        inventory = FakeMcpInventory(EXPECTED_OPENVIKING_MCP_TOOLS)
        runtime = make_runtime(FakeQuery([]), inventory=inventory)
        app = await self.with_lifespan(runtime)

        async with app.router.lifespan_context(app):
            pass

        self.assertEqual(inventory.calls, 1)

    async def test_mcp_inventory_drift_blocks_service_startup(self):
        inventory = FakeMcpInventory([*EXPECTED_OPENVIKING_MCP_TOOLS[:-1], "surprise"])
        runtime = make_runtime(FakeQuery([]), inventory=inventory)
        app = await self.with_lifespan(runtime)

        with self.assertRaises(OpenVikingMcpDriftError) as raised:
            async with app.router.lifespan_context(app):
                pass

        self.assertIn("OpenViking MCP tool inventory drift", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
