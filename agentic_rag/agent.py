"""Claude Agents SDK runtime backed by OpenViking's native MCP endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
import json
from typing import Any
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .config import ConfigurationError, Settings
from .sessions import SESSION_PROJECT_KEY, SessionKey


EXPECTED_OPENVIKING_MCP_TOOLS = (
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
    "health",
)

_READ_ONLY_OPENVIKING_TOOLS = (
    "find",
    "search",
    "read",
    "list",
    "tree",
    "grep",
    "glob",
    "health",
)

_SYSTEM_PROMPT = """You are the Agentic RAG assistant.

For every knowledge-grounded answer:
1. Retrieve relevant material with the OpenViking find or search tool first.
2. Read the cited source with OpenViking read when details matter.
3. Support knowledge-grounded claims with explicit viking:// citations.
4. Clearly refuse and say the knowledge base lacks support when retrieval does
   not provide sufficient evidence. Refuse without viking:// citations; absence
   of support is not a citable fact. Never invent citations or facts.
"""


class OpenVikingMcpInventoryError(RuntimeError):
    """The native OpenViking MCP endpoint could not be inventoried."""


class OpenVikingMcpDriftError(RuntimeError):
    """The running OpenViking MCP tool inventory differs from the build contract."""


def validate_mcp_inventory(
    actual_tools: Iterable[str],
    expected_tools: Sequence[str] = EXPECTED_OPENVIKING_MCP_TOOLS,
) -> dict[str, Any]:
    actual = list(actual_tools)
    expected = list(expected_tools)
    missing = [name for name in expected if name not in actual]
    unexpected = [name for name in actual if name not in expected]
    if missing or unexpected or len(actual) != len(expected):
        details = [f"expected {len(expected)} tools, found {len(actual)}"]
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise OpenVikingMcpDriftError(
            f"OpenViking MCP tool inventory drift ({'; '.join(details)})"
        )
    return {
        "tool_count": len(actual),
        "missing_tools": [],
        "unexpected_tools": [],
    }


class OpenVikingMcpInventory:
    """Dynamic tool inventory client for OpenViking's native HTTP MCP endpoint."""

    def __init__(self, *, settings: Settings):
        self._url = str(settings.openviking_base_url).rstrip("/") + "/mcp"
        self._settings = settings

    async def tool_names(self) -> list[str]:
        headers = self._headers()
        try:
            async with streamablehttp_client(
                self._url,
                headers=headers,
                timeout=5.0,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    names: list[str] = []
                    cursor: str | None = None
                    while True:
                        result = await session.list_tools(cursor)
                        names.extend(tool.name for tool in result.tools)
                        cursor = result.nextCursor
                        if cursor is None:
                            return names
        except OpenVikingMcpInventoryError:
            raise
        except Exception:
            raise OpenVikingMcpInventoryError(
                "OpenViking native MCP endpoint is unavailable or did not return a tool inventory"
            ) from None

    def _headers(self) -> dict[str, str]:
        if not Settings._has_secret(self._settings.openviking_api_key):
            return {}
        return {
            "X-API-Key": self._settings.openviking_api_key.get_secret_value()
        }


class _SdkEventTranslator:
    def __init__(self) -> None:
        self.partial_text_seen = False
        self.partial_thinking_seen = False
        self.tool_names: dict[str, str] = {}
        self.emitted_tool_calls: set[str] = set()
        self.pending_tool_arguments: dict[int, list[str]] = {}
        self.pending_tool_calls: dict[int, tuple[str, str]] = {}

    def translate(self, message: Any) -> list[dict[str, Any]]:
        if isinstance(message, StreamEvent):
            return self._translate_stream_event(message.event)
        if isinstance(message, AssistantMessage):
            return self._translate_assistant_message(message)
        if isinstance(message, UserMessage):
            return self._translate_user_message(message)
        if isinstance(message, ResultMessage) and message.is_error:
            return [{"type": "error", "code": "AGENT_RUNTIME_ERROR"}]
        return []

    def _translate_stream_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = event.get("type")
        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "thinking_delta":
                self.partial_thinking_seen = True
                return [{"type": "thinking_delta", "text": delta.get("thinking", "")}]
            if delta_type == "text_delta":
                self.partial_text_seen = True
                return [{"type": "text_delta", "text": delta.get("text", "")}]
            if delta_type == "input_json_delta":
                index = int(event.get("index", 0))
                self.pending_tool_arguments.setdefault(index, []).append(
                    delta.get("partial_json", "")
                )
            return []

        if event_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                index = int(event.get("index", 0))
                tool_call_id = str(block.get("id", ""))
                tool_name = _openviking_tool_name(str(block.get("name", "")))
                self.tool_names[tool_call_id] = tool_name
                self.pending_tool_calls[index] = (tool_call_id, tool_name)
                arguments = block.get("input") or {}
                if arguments:
                    self.pending_tool_arguments[index] = [json.dumps(arguments)]
            return []

        if event_type != "content_block_stop":
            return []

        index = int(event.get("index", 0))
        fragments = self.pending_tool_arguments.pop(index, None)
        if fragments is None:
            return []
        raw_arguments = "".join(fragments)
        arguments: dict[str, Any]
        try:
            parsed = json.loads(raw_arguments or "{}")
            arguments = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            arguments = {"partial_json": raw_arguments}

        pending_tool = self.pending_tool_calls.pop(index, None)
        if pending_tool is None:
            return []
        tool_call_id, tool_name = pending_tool
        if tool_call_id in self.emitted_tool_calls:
            return []
        self.emitted_tool_calls.add(tool_call_id)
        return [
            {
                "type": "tool_call",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
            }
        ]

    def _translate_assistant_message(
        self, message: AssistantMessage
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, ThinkingBlock) and not self.partial_thinking_seen:
                events.append({"type": "thinking_delta", "text": block.thinking})
            elif isinstance(block, TextBlock) and not self.partial_text_seen:
                events.append({"type": "text_delta", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                tool_name = _openviking_tool_name(block.name)
                self.tool_names[block.id] = tool_name
                if block.id in self.emitted_tool_calls:
                    continue
                self.emitted_tool_calls.add(block.id)
                events.append(
                    {
                        "type": "tool_call",
                        "tool_call_id": block.id,
                        "tool_name": tool_name,
                        "arguments": block.input,
                    }
                )
        return events

    def _translate_user_message(self, message: UserMessage) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        content = message.content if isinstance(message.content, list) else []
        for block in content:
            if not isinstance(block, ToolResultBlock):
                continue
            events.append(
                {
                    "type": "tool_result",
                    "tool_call_id": block.tool_use_id,
                    "tool_name": self.tool_names.get(
                        block.tool_use_id, "openviking_tool"
                    ),
                    "error": bool(block.is_error),
                    "content": block.content,
                }
            )
        return events


def _openviking_tool_name(tool_name: str) -> str:
    return tool_name.removeprefix("mcp__openviking__")


def _claude_runtime_model(model: str) -> str:
    """Return the DeepSeek model with Claude Code's 1M-context marker."""
    return model if model.endswith("[1m]") else f"{model}[1m]"


class ClaudeAgentRuntime:
    """Adapter from Claude Agents SDK subprocess events to the application stream."""

    def __init__(
        self,
        *,
        settings: Settings,
        query: Any = query,
        mcp_inventory: Any | None = None,
    ):
        self.settings = settings
        self.project_key = SESSION_PROJECT_KEY
        self._query = query
        self._mcp_inventory = mcp_inventory or OpenVikingMcpInventory(
            settings=settings
        )

    async def validate_mcp_inventory(self) -> dict[str, Any]:
        return validate_mcp_inventory(await self._mcp_inventory.tool_names())

    async def stream(
        self,
        *,
        session_id: str,
        message: str,
        target_uri: str | None = None,
        session_store: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        try:
            uuid.UUID(session_id)
        except ValueError:
            raise ValueError(
                "session_id must be a UUID for the Claude runtime"
            ) from None

        if session_store is None:
            raise ValueError("The application-owned session store is required")

        key: SessionKey = {
            "project_key": self.project_key,
            "session_id": session_id,
        }
        existing_entries = await session_store.load(key)
        options = self._options(
            session_id=session_id,
            resume=bool(existing_entries),
            session_store=session_store,
        )
        translator = _SdkEventTranslator()
        async for sdk_message in self._query(
            prompt=self._prompt(message, target_uri),
            options=options,
        ):
            for runtime_event in translator.translate(sdk_message):
                yield runtime_event

    def _options(
        self,
        *,
        session_id: str,
        resume: bool,
        session_store: Any,
    ) -> ClaudeAgentOptions:
        if not Settings._has_secret(self.settings.deepseek_api_key):
            raise ConfigurationError(("DEEPSEEK_API_KEY",), missing=True)

        model = _claude_runtime_model(self.settings.deepseek_model)
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=[
                f"mcp__openviking__{name}" for name in _READ_ONLY_OPENVIKING_TOOLS
            ],
            system_prompt=_SYSTEM_PROMPT,
            mcp_servers={"openviking": self._mcp_server_config()},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            model=model,
            env={
                "ANTHROPIC_BASE_URL": str(self.settings.deepseek_base_url),
                "ANTHROPIC_AUTH_TOKEN": self.settings.deepseek_api_key.get_secret_value(),
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "CLAUDE_CODE_SUBAGENT_MODEL": model,
            },
            thinking={"type": "enabled", "budget_tokens": 5000},
            include_partial_messages=True,
            session_store=session_store,
            session_store_flush="eager",
            resume=session_id if resume else None,
            session_id=None if resume else session_id,
            max_turns=20,
        )

    def _mcp_server_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "type": "http",
            "url": str(self.settings.openviking_base_url).rstrip("/") + "/mcp",
        }
        if Settings._has_secret(self.settings.openviking_api_key):
            config["headers"] = {
                "X-API-Key": self.settings.openviking_api_key.get_secret_value()
            }
        return config

    @staticmethod
    def _prompt(message: str, target_uri: str | None) -> str:
        if target_uri is None:
            return message
        return f"Target knowledge scope: {target_uri}\n\nQuestion: {message}"


