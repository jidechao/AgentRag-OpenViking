"""Interactive REPL entrypoint for the shared Agentic RAG application."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import IO
import sys
import uuid

from .agent import ClaudeAgentRuntime
from .application import AgenticRagApplication
from .config import Settings
from .openviking import HttpOpenVikingHealthAdapter, SdkOpenVikingCommandClient
from .sessions import SessionNotFoundError


logger = logging.getLogger(__name__)

_RETRIEVAL_TOOLS = {"find", "search"}
_COMMAND_HELP = (
    "\u53ef\u7528\u547d\u4ee4\uff1a\n"
    "  /new                  \u5f00\u59cb\u4e00\u4e2a\u65b0\u4f1a\u8bdd\n"
    "  /session <session-id> \u5207\u6362\u5230\u5df2\u6709\u4f1a\u8bdd\n"
    "  /current              \u663e\u793a\u5f53\u524d\u4f1a\u8bdd\u6807\u8bc6\n"
    "  /clear                \u6e05\u7a7a\u7ec8\u7aef\u8f93\u51fa\uff08\u4fdd\u7559\u4f1a\u8bdd\u72b6\u6001\uff09\n"
    "  /exit                 \u4e2d\u65ad\u5f53\u524d\u5de5\u4f5c\u5e76\u9000\u51fa\n"
    "\u76f4\u63a5\u8f93\u5165\u95ee\u9898\u53ef\u4e0e OpenViking \u77e5\u8bc6\u5e93\u591a\u8f6e\u95ee\u7b54\u3002"
)


class AgenticRagRepl:
    """Render the Agent Event Stream as a persistent terminal conversation."""

    def __init__(
        self,
        application: AgenticRagApplication,
        *,
        input: Callable[[], str] | None = None,
        output: IO[str] | None = None,
    ):
        self.application = application
        self._input = input
        self._output = output
        self.session_id: str | None = None
        self._active_stream: asyncio.Task[None] | None = None
        self._exiting = False
        self._shutdown_complete = False

    async def run_async(self) -> None:
        self._write("Agentic RAG REPL\n")
        self._write("\u8f93\u5165\u95ee\u9898\uff0c\u6216\u8f93\u5165 /exit \u9000\u51fa\u3002\n\n")
        try:
            while not self._exiting:
                self._write("\u4f60> ", flush=False)
                try:
                    message = self._read_prompt()
                except (EOFError, KeyboardInterrupt):
                    await self.shutdown()
                    break
                stripped = message.strip()
                if not stripped:
                    self._write(_COMMAND_HELP + "\n")
                elif stripped.startswith("/"):
                    await self._handle_command(stripped)
                else:
                    await self._ask(stripped)
        except (KeyboardInterrupt, asyncio.CancelledError):
            await self.shutdown()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Cancel active work and close application-owned adapters once."""
        if self._shutdown_complete:
            return
        self._exiting = True
        active = self._active_stream
        if active is not None and not active.done():
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass

        await self.application.shutdown_jobs()
        await self._aclose(self.application.health_adapter)
        await self._aclose(self.application.command_client)
        self._shutdown_complete = True
        self._write("\n\u5df2\u9000\u51fa Agentic RAG REPL\u3002\n")

    async def _ask(self, message: str) -> None:
        stream = asyncio.create_task(self._consume_stream(message))
        self._active_stream = stream
        try:
            await stream
        except asyncio.CancelledError:
            self._write(
                "\n\u5df2\u4e2d\u65ad\u5f53\u524d\u56de\u7b54\uff1b\u4f1a\u8bdd\u72b6\u6001\u4fdd\u6301\u53ef\u7528\u3002\n"
            )
        finally:
            self._active_stream = None

    async def _consume_stream(self, message: str) -> None:
        thinking_open = False
        answer_open = False
        answer_continued = False

        def close_sections() -> None:
            nonlocal thinking_open, answer_open
            if thinking_open or answer_open:
                self._write("\n")
            thinking_open = False
            answer_open = False

        def begin_answer() -> None:
            nonlocal answer_open, answer_continued
            prefix = "\u56de\u7b54\uff08\u7eed\uff09\uff1a" if answer_continued else "\u56de\u7b54\uff1a"
            self._write(prefix)
            answer_open = True
            answer_continued = True

        async for event in self.application.stream_conversation(
            session_id=self.session_id,
            message=message,
        ):
            event_type = event["type"]
            if event_type == "message_start":
                selected = event.get("session_id")
                if selected is None:
                    continue
                if self.session_id != selected:
                    self.session_id = str(selected)
                    self._write(f"[\u4f1a\u8bdd] \u4f1a\u8bdd\u5df2\u521b\u5efa\uff1a{selected}\n")
            elif event_type == "thinking_delta":
                if answer_open:
                    close_sections()
                if not thinking_open:
                    self._write("[\u601d\u8003] ")
                    thinking_open = True
                self._write(str(event.get("text", "")))
            elif event_type == "text_delta":
                if thinking_open:
                    close_sections()
                if not answer_open:
                    begin_answer()
                self._write(str(event.get("text", "")))
            elif event_type == "tool_call":
                close_sections()
                tool_name = str(event.get("tool_name", ""))
                if tool_name in _RETRIEVAL_TOOLS:
                    self._write("[\u68c0\u7d22] \u6b63\u5728\u67e5\u8be2 OpenViking...\n")
                else:
                    self._write(f"[\u5de5\u5177] {tool_name}...\n")
            elif event_type == "tool_result":
                close_sections()
                tool_name = str(event.get("tool_name", ""))
                state = "\u5931\u8d25" if event.get("error") else "\u5b8c\u6210"
                self._write(f"[\u5de5\u5177\u7ed3\u679c] {tool_name} {state}\n")
            elif event_type == "error":
                close_sections()
                self._write(f"[\u9519\u8bef] {event.get('message', 'Agent runtime failed')}\n")
            elif event_type == "done":
                close_sections()
                citations: list[str] = []
                seen: set[str] = set()
                for citation in event.get("citations", []):
                    if citation not in seen:
                        seen.add(citation)
                        citations.append(str(citation))
                if citations:
                    self._write("\u5f15\u7528\uff1a\n")
                    for citation in citations:
                        self._write(f"- {citation}\n")
                self._write("\n")
        else:
            close_sections()

    async def _handle_command(self, command: str) -> None:
        parts = command.split()
        name = parts[0].lower()
        if name == "/new":
            self.session_id = str(uuid.uuid4())
            self._write(f"[\u4f1a\u8bdd] \u5df2\u5f00\u59cb\u65b0\u4f1a\u8bdd\uff1a{self.session_id}\n")
        elif name == "/current":
            if self.session_id is None:
                self._write(
                    "[\u4f1a\u8bdd] \u5f53\u524d\u6ca1\u6709\u6d3b\u52a8\u4f1a\u8bdd\uff1b"
                    "\u53d1\u9001\u95ee\u9898\u540e\u5c06\u81ea\u52a8\u521b\u5efa\u3002\n"
                )
            else:
                self._write(f"[\u4f1a\u8bdd] \u5f53\u524d\u4f1a\u8bdd\uff1a{self.session_id}\n")
        elif name == "/session":
            await self._switch_session(parts[1:])
        elif name == "/clear":
            self._write("\x1b[2J\x1b[H")
        elif name == "/exit":
            await self.shutdown()
        else:
            self._write(f"\u672a\u77e5\u547d\u4ee4\uff1a{name}\n{_COMMAND_HELP}\n")

    async def _switch_session(self, arguments: list[str]) -> None:
        if len(arguments) != 1:
            self._write("\u7528\u6cd5\uff1a/session <session-id>\n")
            return
        candidate = arguments[0]
        try:
            uuid.UUID(candidate)
        except ValueError:
            self._write("[\u4f1a\u8bdd] \u4f1a\u8bdd\u6807\u8bc6\u5fc5\u987b\u662f UUID\u3002\n")
            return
        try:
            await self.application.get_session(candidate)
        except SessionNotFoundError:
            self._write(f"[\u4f1a\u8bdd] \u4f1a\u8bdd\u4e0d\u5b58\u5728\uff1a{candidate}\n")
            return
        self.session_id = candidate
        self._write(f"[\u4f1a\u8bdd] \u5df2\u5207\u6362\u5230\u4f1a\u8bdd\uff1a{candidate}\n")

    def _read_prompt(self) -> str:
        if self._input is None:
            return input()
        return self._input()

    def _write(self, value: str, *, flush: bool = True) -> None:
        output = self._output if self._output is not None else sys.stdout
        output.write(value)
        if flush:
            output.flush()

    @staticmethod
    async def _aclose(adapter: object) -> None:
        close = getattr(adapter, "aclose", None)
        if close is not None:
            await close()


async def run_repl(settings: Settings | None = None) -> None:
    """Build and validate the real application, then run its REPL."""
    selected_settings = settings or Settings.from_env()
    application = AgenticRagApplication(
        settings=selected_settings,
        health_adapter=HttpOpenVikingHealthAdapter(settings=selected_settings),
        command_client=SdkOpenVikingCommandClient(settings=selected_settings),
        claude_runtime=ClaudeAgentRuntime(settings=selected_settings),
    )
    repl = AgenticRagRepl(application)
    try:
        openapi_document = await application.command_client.get_openapi()
        report = application.validate_command_surface(openapi_document)
        for warning in report["warnings"]:
            logger.warning("OpenViking command surface drift: %s", warning)
        await application.validate_agent_runtime()
        await application.initialize_jobs()
        await repl.run_async()
    finally:
        await repl.shutdown()


def main() -> None:
    """Run the REPL with the repository-configured real adapters."""
    asyncio.run(run_repl())


if __name__ == "__main__":
    main()
