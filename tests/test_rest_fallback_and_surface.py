import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from openviking_sdk.errors import OpenVikingError

from agentic_rag.application import AgenticRagApplication
from agentic_rag.commands import CommandCatalog, CommandSurfaceDriftError
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.openviking import SdkOpenVikingCommandClient
from agentic_rag.service import create_app


class FakeOpenVikingHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeRestCommandClient:
    def __init__(self, results=None, error=None, openapi=None):
        self.results = results or {}
        self.error = error
        self.calls = []
        self.openapi_calls = 0
        self.openapi = openapi

    async def call(self, method, arguments):
        raise AssertionError("SDK calls must not be used for REST fallback commands")

    async def call_rest(self, method, path, *, path_parameters, query_parameters, body):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "path_parameters": dict(path_parameters),
                "query_parameters": dict(query_parameters),
                "body": body,
            }
        )
        if self.error is not None:
            raise self.error
        return self.results.get((method, path), {})

    async def get_openapi(self):
        self.openapi_calls += 1
        return self.openapi if self.openapi is not None else openapi_for_catalog()


def make_application(command_client):
    settings = Settings.from_env(environ={}, env_file=Path("does-not-exist.env"))
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeOpenVikingHealthAdapter(),
        command_client=command_client,
    )


def openapi_for_catalog(
    *,
    mutate_path=None,
    mutate_description=False,
    mutate_body=False,
    mutate_constraint=False,
    mutate_sdk_route=False,
    mutate_sdk_schema=False,
):
    catalog = CommandCatalog.load()
    paths = {}
    for command in catalog.commands:
        for operation_id, (method, path) in (command.http_routes or {}).items():
            if mutate_path is not None and command.name == "cp" and operation_id == command.http_operation:
                path = mutate_path
            if mutate_sdk_route and operation_id == "health_check_health_get":
                path = "/moved-health"
            stored_schema = copy.deepcopy(
                (command.http_operation_schemas or {})[operation_id]
            )
            if mutate_body and command.name == "cp" and operation_id == command.http_operation:
                stored_schema["request_body"]["properties"].pop("to_uri", None)
            if mutate_constraint and command.name == "compile":
                stored_schema["request_body"]["properties"]["from"].pop("minItems", None)
            if mutate_sdk_schema and operation_id == "write_api_v1_content_write_post":
                stored_schema["request_body"]["properties"].pop("uri", None)
            operation = {
                "operationId": operation_id,
                "description": (
                    "changed"
                    if mutate_description
                    and command.name == "cp"
                    and operation_id == command.http_operation
                    else command.description
                ),
                "parameters": [
                    {
                        "name": item["name"],
                        "in": item["in"],
                        "required": item["required"],
                        "schema": item["schema"],
                    }
                    for item in stored_schema["parameters"]
                ],
            }
            if (
                stored_schema["request_body"] is not None
                or stored_schema["request_body_required"]
            ):
                operation["requestBody"] = {
                    "required": stored_schema["request_body_required"],
                    "content": (
                        {"multipart/form-data": {"schema": {}}}
                        if stored_schema["request_body"] is None
                        else {"application/json": {"schema": stored_schema["request_body"]}}
                    ),
                }
            paths.setdefault(path, {})[method.lower()] = operation
    return {"openapi": "3.1.0", "paths": paths}



class RestFallbackManifestTests(unittest.TestCase):
    def test_every_fallback_command_maps_to_an_actual_http_operation(self):
        catalog = CommandCatalog.load()
        fallback = [
            command for command in catalog.commands if command.transport == "rest_fallback"
        ]

        self.assertEqual(len(fallback), 24)
        for command in fallback:
            with self.subTest(command=command.name):
                self.assertTrue(command.http_operation)
                self.assertTrue(command.http_method)
                self.assertTrue(command.http_path)
                self.assertIsNotNone(command.http_parameters)
                self.assertEqual(command.input_schema["type"], "object")

        self.assertEqual(catalog.get("cp").http_operation, "cp_api_v1_fs_cp_post")
        self.assertEqual(
            catalog.get("privacy upsert").http_operation,
            "upsert_privacy_config_api_v1_privacy_configs__category___target_key__post",
        )
        self.assertEqual(
            catalog.get("compile").http_operation, "create_compile_api_v1_compile_post"
        )
        self.assertEqual(catalog.get("chat").http_operation, "chat_bot_v1_chat_post")
        self.assertNotIn("system crypto init-key", catalog.names)

        for command in catalog.commands:
            if command.transport == "sdk":
                with self.subTest(command=command.name):
                    self.assertTrue(command.http_operations)
                    self.assertEqual(set(command.http_operations), set(command.http_routes or {}))

    def test_openapi_schemas_are_flattened_into_command_input_models(self):
        command = CommandCatalog.load().get("privacy get")

        self.assertEqual(
            command.input_schema,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "target_key": {"type": "string"},
                },
                "required": ["category", "target_key"],
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            command.http_parameters,
            {
                "category": ("path", {"type": "string"}),
                "target_key": ("path", {"type": "string"}),
            },
        )


class RestFallbackExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_query_and_body_parameters_are_mapped_by_manifest(self):
        client = FakeRestCommandClient(
            results={("POST", "/api/v1/fs/cp"): {"uri": "viking://copy"}}
        )
        application = make_application(client)

        envelope = await application.execute_command(
            "cp",
            {"from_uri": "viking://a", "to_uri": "viking://b", "recursive": True},
            request_id="rest-cp",
        )

        self.assertTrue(envelope.ok, envelope.as_dict())
        self.assertEqual(envelope.data, {"uri": "viking://copy"})
        self.assertEqual(
            client.calls,
            [
                {
                    "method": "POST",
                    "path": "/api/v1/fs/cp",
                    "path_parameters": {},
                    "query_parameters": {},
                    "body": {
                        "from_uri": "viking://a",
                        "to_uri": "viking://b",
                        "recursive": True,
                    },
                }
            ],
        )

    async def test_path_and_query_fallback_commands_use_the_rest_surface(self):
        client = FakeRestCommandClient()
        application = make_application(client)

        await application.execute_command(
            "privacy get",
            {"category": "memory", "target_key": "user"},
            request_id="privacy",
        )
        await application.execute_command(
            "abstract", {"uri": "viking://demo"}, request_id="abstract"
        )

        self.assertEqual(
            [call["path"] for call in client.calls],
            [
                "/api/v1/privacy-configs/{category}/{target_key}",
                "/api/v1/content/abstract",
            ],
        )
        self.assertEqual(
            client.calls[0]["path_parameters"],
            {"category": "memory", "target_key": "user"},
        )
        self.assertEqual(client.calls[1]["query_parameters"], {"uri": "viking://demo"})

    async def test_rest_error_is_returned_as_structured_command_error(self):
        client = FakeRestCommandClient(
            error=OpenVikingError(
                "target missing",
                code="NOT_FOUND",
                details={"resource": "viking://missing"},
            )
        )
        application = make_application(client)

        envelope = await application.execute_command(
            "privacy get",
            {"category": "memory", "target_key": "missing"},
            request_id="error",
        )

        self.assertFalse(envelope.ok)
        self.assertEqual(envelope.error.code, "OPENVIKING_NOT_FOUND")
        self.assertEqual(envelope.error.message, "target missing")
        self.assertEqual(envelope.error.details["resource"], "viking://missing")


    async def test_compile_and_add_memory_use_rest_jobs_without_shelling_out(self):
        client = FakeRestCommandClient(
            results={
                ("POST", "/api/v1/compile"): {"task_id": "compile-task"},
                ("POST", "/api/v1/sessions"): {"session_id": "memory-session"},
                (
                    "POST",
                    "/api/v1/sessions/{session_id}/messages/batch",
                ): {"messages": []},
                ("POST", "/api/v1/sessions/{session_id}/commit"): {
                    "task_id": "memory-commit-task"
                },
            }
        )
        application = make_application(client)

        compile_envelope = await application.execute_command(
            "compile",
            {
                "from": ["viking://source"],
                "to": "viking://wiki",
                "skill": "viking://skill",
            },
            request_id="compile",
        )
        memory_envelope = await application.execute_command(
            "add-memory", {"content": "remember Alice"}, request_id="memory"
        )

        self.assertTrue(compile_envelope.ok, compile_envelope.as_dict())
        self.assertEqual(
            compile_envelope.data["job"]["openviking_task_id"], "compile-task"
        )
        self.assertTrue(memory_envelope.ok, memory_envelope.as_dict())
        self.assertEqual(
            memory_envelope.data["job"]["openviking_task_id"], "memory-commit-task"
        )
        self.assertEqual(
            [(call["method"], call["path"]) for call in client.calls],
            [
                ("POST", "/api/v1/compile"),
                ("POST", "/api/v1/sessions"),
                ("POST", "/api/v1/sessions/{session_id}/messages/batch"),
                ("POST", "/api/v1/sessions/{session_id}/commit"),
            ],
        )

    async def test_chat_attrs_and_watch_commands_preserve_cli_semantics(self):
        client = FakeRestCommandClient(
            results={("GET", "/api/v1/fs/attrs"): {"attrs": {"tags": ["demo"]}}}
        )
        application = make_application(client)

        await application.execute_command(
            "chat", {"message": "hello"}, request_id="chat"
        )
        await application.execute_command(
            "attrs get",
            {"uri": "viking://demo", "key": "tags"},
            request_id="attrs",
        )
        await application.execute_command(
            "task watch pause", {"to_uri": "viking://demo"}, request_id="watch"
        )
        invalid = await application.execute_command(
            "task watch pause", {}, request_id="watch-invalid"
        )

        self.assertEqual(client.calls[0]["body"]["message"], "hello")
        self.assertIn("session_id", client.calls[0]["body"])
        self.assertEqual(client.calls[1]["body"], {})
        self.assertEqual(
            client.calls[2]["path"], "/api/v1/watches"
        )
        self.assertEqual(
            client.calls[2]["query_parameters"], {"to_uri": "viking://demo"}
        )
        self.assertEqual(client.calls[2]["body"], {"is_active": False})
        self.assertFalse(invalid.ok)
        self.assertEqual(invalid.error.code, "VALIDATION_ERROR")
        self.assertEqual(invalid.error.details["errors"][0]["code"], "exactly_one_of")

    async def test_openapi_constraints_are_validated(self):
        application = make_application(FakeRestCommandClient())

        envelope = await application.execute_command(
            "compile",
            {"from": [], "to": "wiki", "skill": "skill"},
            request_id="invalid-compile",
        )

        self.assertFalse(envelope.ok)
        self.assertEqual(envelope.error.code, "VALIDATION_ERROR")
        self.assertEqual(envelope.error.details["errors"][0]["path"], "from")


class ManifestGenerationTests(unittest.TestCase):
    def test_committed_manifest_matches_installed_cli_sdk_and_openapi_sources(self):
        from scripts.generate_command_manifest import build_manifest

        generated = build_manifest()
        committed = json.loads(
            Path("agentic_rag/command_manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(generated["commands"], committed["commands"])
        self.assertEqual(len(committed["commands"]), 107)
        names = {command["name"] for command in committed["commands"]}
        self.assertNotIn("config", names)
        self.assertNotIn("language", names)
        self.assertNotIn("version", names)
        self.assertNotIn("tui", names)
        self.assertNotIn("system crypto init-key", names)


class SdkOpenVikingCommandClientRestTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_adapter_renders_paths_and_unwraps_command_results(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200, json={"status": "ok", "result": {"value": "result"}}
            )

        adapter = SdkOpenVikingCommandClient(
            settings=Settings.from_env(
                environ={"OPENVIKING_API_KEY": "openviking-secret"},
                env_file=Path("does-not-exist.env"),
            ),
            client=object(),
            rest_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://testserver"
            ),
        )

        result = await adapter.call_rest(
            "GET",
            "/api/v1/privacy-configs/{category}/{target_key}",
            path_parameters={"category": "memory", "target_key": "user"},
            query_parameters={},
            body={},
        )

        self.assertEqual(result, {"value": "result"})
        self.assertEqual(
            requests[0].url.path, "/api/v1/privacy-configs/memory/user"
        )
        self.assertEqual(requests[0].headers["X-API-Key"], "openviking-secret")

    async def test_rest_adapter_translates_http_errors_and_reads_openapi(self):
        def handler(request):
            if request.url.path == "/openapi.json":
                return httpx.Response(200, json={"paths": {}})
            return httpx.Response(404, json={"detail": "target missing"})

        adapter = SdkOpenVikingCommandClient(
            settings=Settings.from_env(environ={}, env_file=Path("does-not-exist.env")),
            client=object(),
            rest_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), base_url="http://testserver"
            ),
        )

        with self.assertRaises(OpenVikingError) as raised:
            await adapter.call_rest(
                "GET",
                "/api/v1/privacy-configs/memory/missing",
                path_parameters={},
                query_parameters={},
                body={},
            )
        openapi = await adapter.get_openapi()

        self.assertEqual(raised.exception.code, "NOT_FOUND")
        self.assertEqual(raised.exception.message, "target missing")
        self.assertEqual(openapi, {"paths": {}})


class CommandSurfaceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_surface_passes_and_reports_metadata_only_differences(self):
        application = make_application(FakeRestCommandClient())

        report = application.validate_command_surface(openapi_for_catalog())
        changed = application.validate_command_surface(
            openapi_for_catalog(mutate_description=True)
        )

        self.assertEqual(report["warnings"], [])
        self.assertEqual(len(changed["warnings"]), 1)
        self.assertIn("description", changed["warnings"][0])

    async def test_material_route_and_body_drift_fail_loudly(self):
        application = make_application(FakeRestCommandClient())

        sdk_drift = openapi_for_catalog()
        del sdk_drift["paths"]["/health"]
        for label, drifted in (
            ("route", openapi_for_catalog(mutate_path="/api/v1/fs/copy")),
            ("body", openapi_for_catalog(mutate_body=True)),
            ("sdk_operation", sdk_drift),
            ("sdk_route", openapi_for_catalog(mutate_sdk_route=True)),
            ("constraint", openapi_for_catalog(mutate_constraint=True)),
            ("sdk_schema", openapi_for_catalog(mutate_sdk_schema=True)),
        ):
            with self.subTest(drift=label):
                with self.assertRaises(CommandSurfaceDriftError) as raised:
                    application.validate_command_surface(drifted)

                expected_name = {
                    "sdk_operation": "health",
                    "sdk_route": "health",
                    "constraint": "compile",
                    "sdk_schema": "write",
                }.get(label, "cp")
                self.assertIn(expected_name, str(raised.exception))
                self.assertIn("OpenViking command surface drift", str(raised.exception))

    async def test_material_drift_prevents_service_startup(self):
        client = FakeRestCommandClient(
            openapi=openapi_for_catalog(mutate_sdk_route=True)
        )
        application = make_application(client)
        app = create_app(application=application)

        with self.assertRaises(RuntimeError) as raised:
            async with app.router.lifespan_context(app):
                pass

        self.assertIn("health", str(raised.exception))

    async def test_service_startup_validates_the_running_openapi_surface(self):
        client = FakeRestCommandClient()
        application = make_application(client)
        app = create_app(application=application)

        async with app.router.lifespan_context(app):
            pass

        self.assertEqual(client.openapi_calls, 1)
        self.assertEqual(len(client.calls), 0)
    async def test_unreachable_openviking_still_starts_and_health_reports_503(self):
        class UnreachableOpenApiClient:
            def __init__(self):
                self.openapi_calls = 0

            async def get_openapi(self):
                self.openapi_calls += 1
                raise OpenVikingError(
                    "OpenViking OpenAPI document is unreachable", code="UNAVAILABLE"
                )

        class DownHealthAdapter:
            async def check(self):
                return DependencyHealth(
                    status=DependencyStatus.DOWN,
                    reachable=False,
                    detail="OpenViking Server is unreachable",
                )

        with TemporaryDirectory() as directory:
            settings = Settings.from_env(
                environ={"SESSION_DATABASE_PATH": str(Path(directory) / "sessions.sqlite3")},
                env_file=Path("does-not-exist.env"),
            )
            application = AgenticRagApplication(
                settings=settings,
                health_adapter=DownHealthAdapter(),
                command_client=UnreachableOpenApiClient(),
            )
            app = create_app(application=application)
            transport = httpx.ASGITransport(app=app)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as http:
                    response = await http.get("/health")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(
            response.json()["dependencies"]["openviking_server"]["status"], "down"
        )

    async def test_unreachable_openviking_with_healthy_probe_starts_and_serves(self):
        class UnreachableOpenApiClient:
            async def get_openapi(self):
                raise OpenVikingError(
                    "OpenViking OpenAPI document is unreachable", code="UNAVAILABLE"
                )

        with TemporaryDirectory() as directory:
            settings = Settings.from_env(
                environ={"SESSION_DATABASE_PATH": str(Path(directory) / "sessions.sqlite3")},
                env_file=Path("does-not-exist.env"),
            )
            application = AgenticRagApplication(
                settings=settings,
                health_adapter=FakeOpenVikingHealthAdapter(),
                command_client=UnreachableOpenApiClient(),
            )
            app = create_app(application=application)
            transport = httpx.ASGITransport(app=app)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as http:
                    catalog = await http.get("/commands")

        self.assertEqual(catalog.status_code, 200, catalog.text)

    async def test_unexpected_openapi_failure_still_fails_startup_loudly(self):
        class BrokenOpenApiClient:
            async def get_openapi(self):
                raise OpenVikingError(
                    "OpenViking OpenAPI document has an invalid shape", code="INTERNAL"
                )

        application = make_application(BrokenOpenApiClient())
        app = create_app(application=application)

        with self.assertRaises(OpenVikingError):
            async with app.router.lifespan_context(app):
                pass


if __name__ == "__main__":
    unittest.main()
