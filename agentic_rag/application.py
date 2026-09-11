"""The single application boundary consumed by REST, REPL, and tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, replace
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import inspect
import uuid

from openviking_sdk.errors import OpenVikingError

from .commands import (
    CommandCatalog,
    CommandEnvelope,
    CommandValidationError,
    json_safe,
)
from .config import ConfigurationError, Settings
from .events import event_timestamp, extract_viking_citations
from .jobs import SqliteJobStore
from .health import (
    ApplicationHealth,
    DependencyHealth,
    DependencyStatus,
    HealthReport,
    HealthStatus,
)
from .sessions import (
    SESSION_PROJECT_KEY,
    SessionNotFoundError,
    SqliteSessionStore,
)


@dataclass
class CommandUpload:
    filename: str
    content_type: str | None
    file: Any


_UNSAFE_FILENAME_CHARACTERS = set('<>:"/\\|?*\x00')
_RESERVED_WINDOWS_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _safe_upload_filename(value: str) -> str:
    name = Path(value).name
    name = "".join("_" if char in _UNSAFE_FILENAME_CHARACTERS else char for char in name)
    name = name[-128:].strip(" .")
    if Path(name).stem.upper().rstrip(" .") in _RESERVED_WINDOWS_STEMS:
        name = "_" + name
    return name or "upload.bin"


def _request_body_accepts_upload(command: Any) -> bool:
    schemas = getattr(command, "http_operation_schemas", None) or {}
    for schema in schemas.values():
        body = schema.get("request_body") if isinstance(schema, Mapping) else None
        properties = body.get("properties") if isinstance(body, Mapping) else None
        if isinstance(properties, Mapping) and "upload" in properties:
            return True
    return False


class AgenticRagApplication:
    """Primary external seam for all Agentic RAG behavior."""

    def __init__(
        self,
        *,
        settings: Settings,
        health_adapter: Any,
        command_client: Any = None,
        command_catalog: CommandCatalog | None = None,
        claude_runtime: Any = None,
        session_store: Any = None,
    ):
        self.settings = settings
        self.health_adapter = health_adapter
        self.command_client = command_client
        self.command_catalog = command_catalog or CommandCatalog.load()
        self.claude_runtime = claude_runtime
        self.session_store = session_store or SqliteSessionStore(
            self.settings.session_database_path
        )
        self.job_store = SqliteJobStore(self.settings.session_database_path)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._job_tasks: dict[str, asyncio.Task[None]] = {}

    async def initialize_jobs(self) -> None:
        await self.job_store.startup()

    async def shutdown_jobs(self) -> None:
        tasks = list(self._job_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def validate_agent_runtime(self) -> None:
        validate = getattr(self.claude_runtime, "validate_mcp_inventory", None)
        if validate is not None:
            await validate()

    async def stream_conversation(
        self,
        *,
        session_id: str | None,
        message: str,
        target_uri: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        request_id = str(uuid.uuid4())
        selected_session_id = session_id or str(uuid.uuid4())
        session_lock = self._session_lock(selected_session_id)
        citations: list[str] = []
        seen_citations: set[str] = set()
        answer_text: list[str] = []
        terminal_error = False

        def event(event_type: str, **fields: Any) -> dict[str, Any]:
            return {
                "type": event_type,
                "request_id": request_id,
                "session_id": selected_session_id,
                "timestamp": event_timestamp(),
                **fields,
            }

        def emit_citations(value: Any) -> list[dict[str, Any]]:
            emitted = []
            for citation in extract_viking_citations(value):
                if citation in seen_citations:
                    continue
                seen_citations.add(citation)
                citations.append(citation)
                emitted.append(event("citation", citation=citation))
            return emitted

        try:
            self._require_model_credentials()
            if self.claude_runtime is None:
                raise RuntimeError("Claude runtime is not configured")

            async with session_lock:
                yield event("message_start")
                runtime_stream = self.claude_runtime.stream(
                    session_id=selected_session_id,
                    message=message,
                    target_uri=target_uri,
                    session_store=self.session_store,
                )
                async for raw_event in runtime_stream:
                    if terminal_error:
                        continue
                    raw_type = raw_event.get("type")
                    if raw_type == "thinking_delta":
                        yield event("thinking_delta", text=self._redact_text(raw_event["text"]))
                    elif raw_type == "text_delta":
                        text = self._redact_text(raw_event["text"])
                        answer_text.append(text)
                        yield event("text_delta", text=text)
                        for citation_event in emit_citations(text):
                            yield citation_event
                    elif raw_type == "tool_call":
                        yield event(
                            "tool_call",
                            tool_call_id=raw_event["tool_call_id"],
                            tool_name=raw_event["tool_name"],
                            arguments=self._redact_json(json_safe(raw_event.get("arguments", {}))),
                        )
                    elif raw_type == "tool_result":
                        content = self._redact_json(json_safe(raw_event.get("content")))
                        yield event(
                            "tool_result",
                            tool_call_id=raw_event["tool_call_id"],
                            tool_name=raw_event["tool_name"],
                            error=bool(raw_event.get("error", False)),
                            content=content,
                        )
                        for citation_event in emit_citations(content):
                            yield citation_event
                    elif raw_type == "error":
                        code = raw_event.get("code")
                        stable_code = (
                            code
                            if isinstance(code, str)
                            and code.isupper()
                            and code.replace("_", "").isalnum()
                            else "AGENT_RUNTIME_ERROR"
                        )
                        terminal_error = True
                        yield event(
                            "error",
                            code=stable_code,
                            message="Agent runtime failed",
                        )
                        continue
                if terminal_error:
                    return
                for citation_event in emit_citations("".join(answer_text)):
                    yield citation_event
                yield event("done", citations=citations)
        except ConfigurationError as error:
            yield event("error", code="CONFIGURATION_ERROR", message=str(error))
        except Exception:
            if not terminal_error:
                yield event(
                    "error", code="AGENT_RUNTIME_ERROR", message="Agent runtime failed"
                )

    async def execute_command(
        self,
        command_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        upload: CommandUpload | None = None,
    ) -> CommandEnvelope:
        selected_request_id = request_id or str(uuid.uuid4())
        try:
            command = self.command_catalog.get(command_name)
        except KeyError:
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="UNKNOWN_COMMAND",
                message=f"Unknown OpenViking command: {command_name}",
            )

        supplied = {} if arguments is None else dict(arguments)
        upload_argument = self._upload_argument_name(command.name)
        if upload is not None:
            if command.file_input == "output":
                return CommandEnvelope.failure(
                    command=command_name,
                    request_id=selected_request_id,
                    code="UPLOAD_NOT_SUPPORTED",
                    message="Command does not accept an uploaded input file",
                )
            if command.file_input == "input_required" and upload_argument not in supplied:
                supplied[upload_argument] = ""
            if command.file_input == "input_optional" and upload_argument not in supplied:
                supplied[upload_argument] = ""

        try:
            validated = self.command_catalog.validate_arguments(command, supplied)
        except CommandValidationError as error:
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="VALIDATION_ERROR",
                message="Command arguments do not match the command schema",
                details={"errors": error.errors},
            )

        if command.http_bridge == "watch_toggle":
            supplied_identifiers = [
                name for name in ("task_id", "to_uri") if validated.get(name) is not None
            ]
            if len(supplied_identifiers) != 1:
                return CommandEnvelope.failure(
                    command=command_name,
                    request_id=selected_request_id,
                    code="VALIDATION_ERROR",
                    message="Command arguments do not match the command schema",
                    details={
                        "errors": [
                            {
                                "path": "task_id",
                                "code": "exactly_one_of",
                                "message": "Exactly one of task_id or to_uri is required",
                            }
                        ]
                    },
                )

        if command.transport not in {"sdk", "rest_fallback"}:
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="COMMAND_UNSUPPORTED",
                message="Command transport is not supported",
            )
        if (
            command.transport == "rest_fallback"
            and self.command_client is not None
            and not hasattr(self.command_client, "call_rest")
        ):
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="REST_CLIENT_UNCONFIGURED",
                message="OpenViking REST command client is not configured",
            )
        if self.command_client is None:
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="COMMAND_CLIENT_UNCONFIGURED",
                message="OpenViking command client is not configured",
            )

        if command.asynchronous == "native_async":
            if "wait" in command.input_schema.get("properties", {}):
                validated["wait"] = False
                validated.pop("timeout", None)
            try:
                result = await self._invoke_command(command, validated, upload)
            except OpenVikingError as error:
                return self._openviking_failure(
                    command_name, selected_request_id, error
                )
            except Exception:
                return CommandEnvelope.failure(
                    command=command_name,
                    request_id=selected_request_id,
                    code="INTERNAL_ERROR",
                    message="OpenViking command execution failed",
                )

            task_id = _extract_task_id(result)
            provider_status = None if task_id else _provider_status(result)
            job_status = "running" if task_id else (provider_status or "succeeded")
            job_result = None if job_status == "failed" else result
            job_error = None
            if job_status == "failed":
                provider_error = (
                    result.get("error") if isinstance(result, Mapping) else None
                )
                if isinstance(provider_error, Mapping):
                    code = provider_error.get("code")
                    message = provider_error.get("message")
                    job_error = {
                        "code": f"OPENVIKING_{code}"
                        if isinstance(code, str) and code
                        else "OPENVIKING_ERROR",
                        "message": message
                        if isinstance(message, str) and message
                        else "OpenViking command failed",
                    }
                    if "details" in provider_error:
                        job_error["details"] = provider_error["details"]
                else:
                    job_error = {
                        "code": "OPENVIKING_ERROR",
                        "message": provider_error
                        if isinstance(provider_error, str) and provider_error
                        else "OpenViking command failed",
                        "details": result,
                    }
            job = await self.job_store.create(
                job_id=str(uuid.uuid4()),
                request_id=selected_request_id,
                command=command_name,
                arguments=self._redact_json(json_safe(validated)),
                status=job_status,
                openviking_task_id=task_id,
                result=self._redact_json(json_safe(job_result)),
                error=self._redact_json(json_safe(job_error)),
            )
            return CommandEnvelope.success(
                data={"job": self._redact_json(json_safe(job))},
                command=command_name,
                request_id=selected_request_id,
            )

        if command.asynchronous in {"blocking_wait", "long_running"}:
            job_id = str(uuid.uuid4())
            job = await self.job_store.create(
                job_id=job_id,
                request_id=selected_request_id,
                command=command_name,
                arguments=self._redact_json(json_safe(validated)),
                status="queued",
            )
            task = asyncio.create_task(
                self._run_local_job(
                    job_id,
                    command,
                    validated,
                    selected_request_id,
                    upload,
                )
            )
            self._job_tasks[job_id] = task
            task.add_done_callback(lambda _: self._job_tasks.pop(job_id, None))
            return CommandEnvelope.success(
                data={"job": self._redact_json(json_safe(job))},
                command=command_name,
                request_id=selected_request_id,
            )

        try:
            result = await self._invoke_command(command, validated, upload)
        except OpenVikingError as error:
            return self._openviking_failure(command_name, selected_request_id, error)
        except Exception:
            return CommandEnvelope.failure(
                command=command_name,
                request_id=selected_request_id,
                code="INTERNAL_ERROR",
                message="OpenViking command execution failed",
            )

        return CommandEnvelope.success(
            data=self._redact_json(json_safe(result)),
            command=command_name,
            request_id=selected_request_id,
        )

    async def _run_local_job(
        self,
        job_id: str,
        command: Any,
        arguments: dict[str, Any],
        request_id: str,
        upload: CommandUpload | None,
    ) -> None:
        try:
            await self.job_store.mark_running(job_id)
            result = await self._invoke_command(command, arguments, upload)
            await self.job_store.succeed(
                job_id, self._redact_json(json_safe(result))
            )
        except asyncio.CancelledError:
            await self.job_store.interrupt(
                job_id,
                {
                    "code": "JOB_INTERRUPTED",
                    "message": "Local command job was cancelled",
                },
            )
            raise
        except OpenVikingError as error:
            await self.job_store.fail(
                job_id,
                {
                    "code": f"OPENVIKING_{error.code}",
                    "message": self._redact_text(error.message),
                },
            )
        except Exception:
            await self.job_store.fail(
                job_id,
                {
                    "code": "INTERNAL_ERROR",
                    "message": "OpenViking command execution failed",
                },
            )

    @asynccontextmanager
    async def _staged_upload(
        self, command: Any, arguments: dict[str, Any], upload: CommandUpload | None
    ):
        if upload is None:
            yield
            return

        argument_name = self._upload_argument_name(command.name)
        with TemporaryDirectory(prefix="agentic-rag-upload-") as directory:
            staged_path = Path(directory) / _safe_upload_filename(upload.filename)
            with staged_path.open("wb") as target:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if inspect.iscoroutine(chunk):
                        chunk = await chunk
                    if not chunk:
                        break
                    target.write(chunk)

            arguments[argument_name] = str(staged_path)
            if (
                "options" in command.input_schema.get("properties", {})
                and _request_body_accepts_upload(command)
            ):
                options = arguments.get("options")
                if not isinstance(options, dict):
                    options = {}
                    arguments["options"] = options
                extra = options.get("extra")
                if not isinstance(extra, dict):
                    extra = {}
                    options["extra"] = extra
                extra["upload"] = {
                    "filename": staged_path.name,
                    "content_type": upload.content_type,
                }
            try:
                yield
            finally:
                arguments[argument_name] = str(staged_path)

    async def _invoke_command(
        self,
        command: Any,
        arguments: dict[str, Any],
        upload: CommandUpload | None,
    ) -> Any:
        if command.transport == "sdk":
            async with self._staged_upload(command, arguments, upload):
                return await self.command_client.call(command.sdk_method, arguments)
        return await self._call_rest_command(command, arguments)

    async def _call_rest_command(self, command: Any, arguments: dict[str, Any]) -> Any:
        if command.http_bridge == "add_memory":
            return await self._call_add_memory(command, arguments)

        values = dict(arguments)
        filter_key = values.pop("key", None) if command.http_bridge == "attrs_filter" else None
        method = command.http_method
        path = command.http_path
        path_parameters: dict[str, Any] = {}
        query_parameters: dict[str, Any] = {}
        body: dict[str, Any] = {}

        if command.http_bridge == "watch_toggle":
            body = {"is_active": command.name == "task watch resume"}
            routes = command.http_routes or {}
            if values.get("to_uri") is not None:
                operation_id = command.http_operations[1]
                method, path = routes[operation_id]
                query_parameters["to_uri"] = values["to_uri"]
            else:
                path_parameters["task_id"] = values["task_id"]
        else:
            for name, value in values.items():
                location = (command.http_parameters or {}).get(name, ("body", {}))[0]
                if location == "path":
                    path_parameters[name] = value
                elif location == "query":
                    query_parameters[name] = value
                elif location == "body":
                    body[name] = value

        if command.http_bridge == "chat":
            body.setdefault("session_id", str(uuid.uuid4()))
            sender_id = body.pop("sender_id", "user")
            body["user_id"] = sender_id
            if body.get("stream"):
                stream_operation_id = command.http_operations[-1]
                method, path = (command.http_routes or {})[stream_operation_id]

        result = await self.command_client.call_rest(
            method,
            path,
            path_parameters=path_parameters,
            query_parameters=query_parameters,
            body=body,
        )
        if filter_key is not None:
            attributes = result.get("attrs", {}) if isinstance(result, Mapping) else {}
            value = attributes.get(filter_key) if isinstance(attributes, Mapping) else None
            return {
                "uri": arguments.get("uri"),
                "key": filter_key,
                "value": value,
            }
        return result

    async def _call_add_memory(self, command: Any, arguments: dict[str, Any]) -> Any:
        content = arguments["content"]
        if isinstance(content, str):
            messages = [{"role": "user", "content": content}]
        elif isinstance(content, Mapping):
            messages = [dict(content)]
        elif isinstance(content, list):
            messages = content
        else:
            raise OpenVikingError(
                "content must be a message, message object, or message array",
                code="INVALID_ARGUMENT",
            )

        create_method, create_path = (command.http_routes or {})[command.http_operations[0]]
        session = await self.command_client.call_rest(
            create_method,
            create_path,
            path_parameters={},
            query_parameters={},
            body={},
        )
        session_id = session.get("session_id") if isinstance(session, Mapping) else None
        if not isinstance(session_id, str) or not session_id:
            raise OpenVikingError(
                "OpenViking did not return a memory session ID",
                code="INTERNAL",
            )
        batch_method, batch_path = (command.http_routes or {})[command.http_operations[1]]
        await self.command_client.call_rest(
            batch_method,
            batch_path,
            path_parameters={"session_id": session_id},
            query_parameters={},
            body={"messages": messages},
        )
        commit_method, commit_path = (command.http_routes or {})[command.http_operations[2]]
        committed = await self.command_client.call_rest(
            commit_method,
            commit_path,
            path_parameters={"session_id": session_id},
            query_parameters={},
            body={},
        )
        task_id = None
        if isinstance(committed, Mapping):
            task_id = committed.get("task_id")
        if isinstance(task_id, str) and task_id:
            return {"session_id": session_id, "task_id": task_id}
        return {"session_id": session_id, "commit": committed}

    def validate_command_surface(self, openapi_document: Mapping[str, Any]) -> dict[str, Any]:
        return self.command_catalog.validate_against_openapi(openapi_document)

    @staticmethod
    def _upload_argument_name(command_name: str) -> str:
        if command_name in {"add-skill", "skills add", "skills update", "skills validate"}:
            return "data"
        if command_name in {"import", "restore"}:
            return "file_path"
        return "path"

    def _openviking_failure(
        self, command_name: str, request_id: str, error: OpenVikingError
    ) -> CommandEnvelope:
        return CommandEnvelope.failure(
            command=command_name,
            request_id=request_id,
            code=f"OPENVIKING_{error.code}",
            message=self._redact_text(error.message),
            details=self._redact_json(error.details),
        )

    async def list_sessions(self) -> Any:
        summaries = await self.session_store.list_session_summaries(
            SESSION_PROJECT_KEY
        )
        return {"sessions": self._redact_json(json_safe(summaries))}

    async def get_session(self, session_id: str) -> Any:
        key = {
            "project_key": SESSION_PROJECT_KEY,
            "session_id": session_id,
        }
        summaries = await self.session_store.list_session_summaries(
            SESSION_PROJECT_KEY
        )
        summary = next(
            (item for item in summaries if item["session_id"] == session_id), None
        )
        if summary is None:
            raise SessionNotFoundError(session_id)
        entries = (await self.session_store.load(key)) or []
        subkeys = await self.session_store.list_subkeys(key)
        return self._redact_json(
            json_safe(
                {
                    "session_id": session_id,
                    "mtime": summary["mtime"],
                    "summary": summary["data"],
                    "entries": entries,
                    "subkeys": subkeys,
                }
            )
        )

    async def delete_session(self, session_id: str) -> Any:
        async with self._session_lock(session_id):
            summaries = await self.session_store.list_session_summaries(
                SESSION_PROJECT_KEY
            )
            if not any(item["session_id"] == session_id for item in summaries):
                raise SessionNotFoundError(session_id)
            await self.session_store.delete(
                {
                    "project_key": SESSION_PROJECT_KEY,
                    "session_id": session_id,
                }
            )
        return {"deleted": True, "session_id": session_id}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def get_job(self, job_id: str) -> Any:
        try:
            job = await self.job_store.get(job_id)
        except KeyError:
            return None

        task_id = job.get("openviking_task_id")
        if task_id and self.command_client is not None:
            try:
                authoritative = await self.command_client.call(
                    "get_task", {"task_id": task_id}
                )
            except Exception:
                return self._redact_json(json_safe(job))
            provider_status = _provider_status(authoritative)
            if provider_status is not None:
                if isinstance(authoritative, Mapping) and "result" in authoritative:
                    result = authoritative.get("result")
                else:
                    result = authoritative
                job = await self.job_store.refresh(
                    job_id,
                    status=provider_status,
                    result=self._redact_json(json_safe(result)),
                    openviking_task_id=task_id,
                )
        return self._redact_json(json_safe(job))


    async def health(self) -> HealthReport:
        try:
            dependency = await self.health_adapter.check()
        except ConfigurationError as error:
            dependency = DependencyHealth(
                status=DependencyStatus.CONFIGURATION_ERROR,
                reachable=False,
                detail=str(error),
            )
        except Exception:
            dependency = DependencyHealth(
                status=DependencyStatus.DOWN,
                reachable=False,
                detail="OpenViking health check failed",
            )
        dependency = self._redact_dependency(dependency)

        overall_status = (
            HealthStatus.OK
            if dependency.status is DependencyStatus.UP
            else HealthStatus.DEGRADED
        )
        return HealthReport(
            status=overall_status,
            application=ApplicationHealth(status="ready"),
            dependencies={"openviking_server": dependency},
        )

    def _redact_dependency(self, dependency: DependencyHealth) -> DependencyHealth:
        detail = dependency.detail
        if detail is None:
            return dependency
        return replace(dependency, detail=self._redact_text(detail))

    def _redact_text(self, text: str) -> str:
        for secret in (
            self.settings.deepseek_api_key,
            self.settings.openviking_api_key,
        ):
            if Settings._has_secret(secret):
                text = text.replace(secret.get_secret_value(), "[redacted]")
        return text

    def _redact_json(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, Mapping):
            return {str(key): self._redact_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact_json(item) for item in value]
        return value

    def _require_model_credentials(self) -> None:
        if not Settings._has_secret(self.settings.deepseek_api_key):
            raise ConfigurationError(("DEEPSEEK_API_KEY",), missing=True)


def _extract_task_id(result: Any) -> str | None:
    if not isinstance(result, Mapping):
        return None
    for name in ("task_id", "taskId"):
        value = result.get(name)
        if isinstance(value, str) and value:
            return value
    task = result.get("task")
    if isinstance(task, Mapping):
        return _extract_task_id(task)
    return None


def _provider_status(result: Any) -> str | None:
    if not isinstance(result, Mapping):
        return None
    value = result.get("status")
    if not isinstance(value, str):
        return None
    normalized = value.lower().replace("-", "_")
    if normalized in {"queued", "pending"}:
        return "queued"
    if normalized in {"running", "processing"}:
        return "running"
    if normalized in {"succeeded", "success", "completed", "done"}:
        return "succeeded"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized in {"cancelled", "canceled", "interrupted"}:
        return "interrupted"
    return None

