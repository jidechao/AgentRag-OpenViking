"""FastAPI entrypoint. All behavior is delegated to the application seam."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import uuid
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from openviking_sdk.errors import OpenVikingError
from pydantic import BaseModel, Field, ValidationError, field_validator

from .agent import ClaudeAgentRuntime, OpenVikingMcpInventoryError
from .application import AgenticRagApplication, CommandUpload
from .commands import CommandEnvelope
from .config import Settings
from .events import event_timestamp
from .health import HealthStatus
from .openviking import HttpOpenVikingHealthAdapter, SdkOpenVikingCommandClient
from .sessions import SessionNotFoundError

logger = logging.getLogger(__name__)


class CommandExecutionRequest(BaseModel):
    command: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ConversationStreamRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1)
    target_uri: str | None = None

    @field_validator("session_id")
    @classmethod
    def _session_id_must_be_a_uuid(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValueError("session_id must be a UUID") from None
        return value


def create_app(
    *,
    application: AgenticRagApplication | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    if application is not None and settings is not None:
        raise ValueError("Pass either application or settings, not both")

    if application is None:
        selected_settings = settings or Settings.from_env()
        application = AgenticRagApplication(
            settings=selected_settings,
            health_adapter=HttpOpenVikingHealthAdapter(settings=selected_settings),
            command_client=SdkOpenVikingCommandClient(settings=selected_settings),
            claude_runtime=ClaudeAgentRuntime(settings=selected_settings),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # An unreachable OpenViking dependency must not prevent startup:
        # the health endpoint reports it as 503 instead. Verified material
        # drift (OpenAPI surface or MCP tool inventory) still fails loudly.
        try:
            openapi_document = await application.command_client.get_openapi()
        except OpenVikingError as error:
            if error.code != "UNAVAILABLE":
                raise
            logger.warning(
                "OpenViking Server is unreachable at startup; skipping command "
                "surface validation until it recovers"
            )
        else:
            report = application.validate_command_surface(openapi_document)
            for warning in report["warnings"]:
                logger.warning("OpenViking command surface drift: %s", warning)
        try:
            await application.validate_agent_runtime()
        except OpenVikingMcpInventoryError:
            logger.warning(
                "OpenViking MCP endpoint is unreachable at startup; skipping "
                "tool inventory validation until it recovers"
            )
        await application.initialize_jobs()
        yield
        await application.shutdown_jobs()
        if isinstance(application.health_adapter, HttpOpenVikingHealthAdapter):
            await application.health_adapter.aclose()
        if isinstance(application.command_client, SdkOpenVikingCommandClient):
            await application.command_client.aclose()

    fastapi_app = FastAPI(title="Agentic RAG Assistant", lifespan=lifespan)
    fastapi_app.state.agentic_rag = application

    @fastapi_app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse | EventSourceResponse:
        body = error.body if isinstance(error.body, dict) else {}
        if request.url.path == "/qa/stream":
            session_id = body.get("session_id")
            error_event = {
                "type": "error",
                "request_id": str(uuid.uuid4()),
                "session_id": (
                    session_id
                    if isinstance(session_id, str) and session_id
                    else str(uuid.uuid4())
                ),
                "timestamp": event_timestamp(),
                "code": "VALIDATION_ERROR",
                "message": "QA request body does not match the streaming contract",
            }

            async def validation_events() -> AsyncIterator[dict[str, str]]:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        error_event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }

            return EventSourceResponse(validation_events())

        command = body.get("command")
        request_id = body.get("request_id")
        envelope = CommandEnvelope.failure(
            command=command if isinstance(command, str) else "",
            request_id=request_id if isinstance(request_id, str) and request_id else str(uuid.uuid4()),
            code="VALIDATION_ERROR",
            message="Command request body does not match the execution contract",
            details={
                "errors": [
                    {
                        "path": ".".join(str(part) for part in item.get("loc", [])),
                        "code": str(item.get("type", "invalid")),
                        "message": str(item.get("msg", "Request validation failed")),
                    }
                    for item in error.errors()
                ]
            },
        )
        return JSONResponse(status_code=400, content=envelope.as_dict())

    @fastapi_app.get("/health")
    async def health(request: Request) -> JSONResponse:
        report = await application.health()
        return JSONResponse(
            status_code=200 if report.status is HealthStatus.OK else 503,
            content=report.as_dict(),
        )

    @fastapi_app.get("/commands")
    async def command_catalog(request: Request) -> JSONResponse:
        return JSONResponse(content=application.command_catalog.as_dict())

    @fastapi_app.get("/sessions")
    async def list_sessions(request: Request) -> JSONResponse:
        return JSONResponse(content=await application.list_sessions())

    @fastapi_app.get("/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> JSONResponse:
        try:
            session = await application.get_session(session_id)
        except SessionNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_session_error("SESSION_NOT_FOUND"),
            )
        return JSONResponse(content=session)

    @fastapi_app.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request) -> JSONResponse:
        try:
            result = await application.delete_session(session_id)
        except SessionNotFoundError:
            return JSONResponse(
                status_code=404,
                content=_session_error("SESSION_NOT_FOUND"),
            )
        return JSONResponse(content=result)

    @fastapi_app.post("/qa/stream")
    async def stream_qa(
        conversation: ConversationStreamRequest, request: Request
    ) -> EventSourceResponse:
        async def conversation_events() -> AsyncIterator[dict[str, str]]:
            async for event in application.stream_conversation(
                session_id=conversation.session_id,
                message=conversation.message,
                target_uri=conversation.target_uri,
            ):
                yield {
                    "event": event["type"],
                    "data": json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }

        return EventSourceResponse(conversation_events())

    @fastapi_app.post("/commands/execute")
    async def execute_command(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            command = form.get("command")
            request_id = form.get("request_id")
            uploaded = form.get("file")
            raw_arguments = form.get("arguments_json", "{}")
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments_json must contain an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                if uploaded is not None and hasattr(uploaded, "read"):
                    await uploaded.close()
                envelope = CommandEnvelope.failure(
                    command=command if isinstance(command, str) else "",
                    request_id=request_id if isinstance(request_id, str) and request_id else str(uuid.uuid4()),
                    code="VALIDATION_ERROR",
                    message="Command request body does not match the execution contract",
                    details={
                        "errors": [
                            {
                                "path": "arguments_json",
                                "code": "invalid_json_object",
                                "message": "arguments_json must be a JSON object",
                            }
                        ]
                    },
                )
                return JSONResponse(status_code=400, content=envelope.as_dict())

            upload = None
            if uploaded is not None and hasattr(uploaded, "read"):
                upload = CommandUpload(
                    filename=uploaded.filename or "upload.bin",
                    content_type=uploaded.content_type,
                    file=uploaded,
                )
            try:
                envelope = await application.execute_command(
                    command if isinstance(command, str) else "",
                    arguments,
                    request_id=request_id if isinstance(request_id, str) and request_id else None,
                    upload=upload,
                )
            finally:
                if uploaded is not None and hasattr(uploaded, "read"):
                    await uploaded.close()
            return JSONResponse(
                status_code=_http_status(envelope),
                content=envelope.as_dict(),
            )

        try:
            body = await request.json()
            execution = CommandExecutionRequest.model_validate(body)
        except (json.JSONDecodeError, TypeError):
            envelope = CommandEnvelope.failure(
                command="",
                request_id=str(uuid.uuid4()),
                code="VALIDATION_ERROR",
                message="Command request body does not match the execution contract",
            )
            return JSONResponse(status_code=400, content=envelope.as_dict())
        except ValidationError as error:
            command = body.get("command") if isinstance(body, dict) else None
            request_id = body.get("request_id") if isinstance(body, dict) else None
            envelope = CommandEnvelope.failure(
                command=command if isinstance(command, str) else "",
                request_id=request_id if isinstance(request_id, str) and request_id else str(uuid.uuid4()),
                code="VALIDATION_ERROR",
                message="Command request body does not match the execution contract",
                details={
                    "errors": [
                        {
                            "path": ".".join(str(part) for part in item.get("loc", [])),
                            "code": str(item.get("type", "invalid")),
                            "message": str(item.get("msg", "Request validation failed")),
                        }
                        for item in error.errors()
                    ]
                },
            )
            return JSONResponse(status_code=400, content=envelope.as_dict())

        envelope = await application.execute_command(
            execution.command,
            execution.arguments,
            request_id=execution.request_id,
        )
        return JSONResponse(
            status_code=_http_status(envelope),
            content=envelope.as_dict(),
        )

    @fastapi_app.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> JSONResponse:
        job = await application.get_job(job_id)
        if job is None:
            envelope = CommandEnvelope.failure(
                command="",
                request_id=job_id,
                code="JOB_NOT_FOUND",
                message="OpenViking command job not found",
            )
            return JSONResponse(status_code=404, content=envelope.as_dict())
        envelope = CommandEnvelope.success(
            data=job,
            command=job["command"],
            request_id=job["request_id"],
        )
        return JSONResponse(content=envelope.as_dict())

    return fastapi_app


def _http_status(envelope: CommandEnvelope) -> int:
    if envelope.ok:
        return 200
    if envelope.error is None:
        return 500
    if envelope.error.code == "INTERNAL_ERROR":
        return 500
    if envelope.error.code == "JOB_NOT_FOUND":
        return 404
    if envelope.error.code.startswith("OPENVIKING_"):
        return 502
    return 400


def _session_error(code: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": "Persistent conversation session not found",
        }
    }

