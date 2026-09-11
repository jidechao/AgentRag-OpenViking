import asyncio
import io
import shutil
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from openviking_sdk.errors import OpenVikingError

from agentic_rag.application import AgenticRagApplication
from agentic_rag.commands import CommandDefinition
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.service import create_app


@contextmanager
def temporary_directory():
    with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        yield directory
        path = Path(directory)
        for _ in range(20):
            if not path.exists():
                return
            shutil.rmtree(path, ignore_errors=True)
            time.sleep(0.01)


class FakeHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeUpload:
    def __init__(self, data=b"# knowledge base\n", filename="knowledge.md", content_type="text/markdown"):
        self.file = io.BytesIO(data)
        self.filename = filename
        self.content_type = content_type

    async def read(self, size=-1):
        return self.file.read(size)


class FakeCommandClient:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error
        self.calls = []

    async def call(self, method, arguments):
        self.calls.append((method, dict(arguments)))
        if self.error is not None:
            raise self.error
        return dict(self.result)


class BlockingCommandClient:
    def __init__(self, result=None):
        self.result = result or {"uri": "viking://demo/backup"}
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def call(self, method, arguments):
        self.calls.append((method, dict(arguments)))
        self.started.set()
        await self.release.wait()
        return dict(self.result)


def make_application(client, directory):
    settings = Settings.from_env(
        environ={
            "SESSION_DATABASE_PATH": str(Path(directory) / "sessions.sqlite3"),
        },
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeHealthAdapter(),
        command_client=client,
    )


async def wait_for_job(application, job_id, status):
    for _ in range(100):
        job = await application.get_job(job_id)
        if job["status"] == status:
            await asyncio.sleep(0)
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"Job did not reach {status}: {job}")


class _SingleCommandCatalog:
    def __init__(self, command):
        self.command = command
        self.names = [command.name]

    def get(self, name):
        if name != self.command.name:
            raise KeyError(name)
        return self.command

    def validate_arguments(self, command, arguments):
        return dict(arguments)


class FileUploadAndCommandJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_resource_upload_preserves_server_supported_metadata_and_cleans_up(self):
        client = FakeCommandClient(result={"task_id": "openviking-task-1"})
        with temporary_directory() as directory:
            application = make_application(client, directory)
            upload = FakeUpload()

            envelope = await application.execute_command(
                "add-resource",
                {"to": "viking://demo", "wait": True},
                request_id="upload-request",
                upload=upload,
            )

            self.assertTrue(envelope.ok, envelope.as_dict())
            method, arguments = client.calls[0]
            staged_path = Path(arguments["path"])
            self.assertEqual(method, "add_resource")
            self.assertFalse(staged_path.exists())
            self.assertEqual(staged_path.name, "knowledge.md")
            self.assertFalse(arguments["wait"])
            self.assertNotIn("options", arguments)
            job = envelope.data["job"]
            self.assertEqual(job["openviking_task_id"], "openviking-task-1")
            self.assertEqual(job["request_id"], "upload-request")
            self.assertEqual(job["command"], "add-resource")
            self.assertIn(job["status"], {"queued", "running"})
            self.assertIn("created_at", job)
            self.assertIn("updated_at", job)
            self.assertEqual(job["result"], {"task_id": "openviking-task-1"})

    async def test_native_result_without_task_id_completes_instead_of_staying_running(self):
        client = FakeCommandClient(result={"uri": "viking://demo/updated"})
        with temporary_directory() as directory:
            application = make_application(client, directory)

            envelope = await application.execute_command(
                "set-tags", {"uri": "viking://demo", "tags": ["demo"]}
            )

            self.assertTrue(envelope.ok, envelope.as_dict())
            job = envelope.data["job"]
            self.assertEqual(job["status"], "succeeded")
            self.assertIsNone(job["openviking_task_id"])
            self.assertEqual(job["result"], {"uri": "viking://demo/updated"})

    async def test_native_result_status_is_preserved_when_no_task_id_exists(self):
        cases = (
            ({"status": "failed", "error": {"code": "INVALID", "message": "rejected"}}, "failed"),
            ({"status": "queued"}, "queued"),
            ({"status": "running"}, "running"),
            ({"status": "success"}, "succeeded"),
            ({"uri": "viking://demo/final"}, "succeeded"),
        )
        for result, expected_status in cases:
            with self.subTest(result=result):
                with temporary_directory() as directory:
                    application = make_application(
                        FakeCommandClient(result=result), directory
                    )

                    envelope = await application.execute_command(
                        "set-tags", {"uri": "viking://demo", "tags": ["demo"]}
                    )

                    job = envelope.data["job"]
                    self.assertEqual(job["status"], expected_status)
                    if expected_status == "failed":
                        self.assertEqual(job["error"]["code"], "OPENVIKING_INVALID")
                        self.assertEqual(job["error"]["message"], "rejected")
                    else:
                        self.assertEqual(job["result"], result)

    async def test_schema_active_upload_metadata_uses_the_safe_staged_basename(self):
        command = CommandDefinition(
            name="upload-metadata",
            description="Upload with provider metadata",
            transport="sdk",
            sdk_method="upload_metadata",
            http_operation="upload",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "options": {"type": "object"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            asynchronous="native_async",
            file_input="input_optional",
            danger="mutating",
            http_operation_schemas={
                "upload": {
                    "request_body": {"properties": {"upload": {"type": "object"}}}
                }
            },
        )
        client = FakeCommandClient(result={"uri": "viking://demo/uploaded"})
        with temporary_directory() as directory:
            settings = Settings.from_env(
                environ={"SESSION_DATABASE_PATH": str(Path(directory) / "sessions.sqlite3")},
                env_file=Path("does-not-exist.env"),
            )
            application = AgenticRagApplication(
                settings=settings,
                health_adapter=FakeHealthAdapter(),
                command_client=client,
                command_catalog=_SingleCommandCatalog(command),
            )

            for filename, expected_name in (
                ("../knowledge.md", "knowledge.md"),
                ("..\\CON.md", "_CON.md"),
            ):
                with self.subTest(filename=filename):
                    envelope = await application.execute_command(
                        command.name,
                        {},
                        upload=FakeUpload(filename=filename),
                    )

                    self.assertTrue(envelope.ok, envelope.as_dict())
                    staged_path = Path(client.calls[0][1]["path"])
                    metadata = client.calls[0][1]["options"]["extra"]["upload"]
                    self.assertEqual(staged_path.name, expected_name)
                    self.assertEqual(metadata["filename"], expected_name)
                    self.assertEqual(metadata["content_type"], "text/markdown")
                    client.calls.clear()

    async def test_failed_upload_cleans_the_staged_file_and_returns_structured_error(self):
        client = FakeCommandClient(
            error=OpenVikingError("upload failed", code="INVALID_INPUT")
        )
        with temporary_directory() as directory:
            application = make_application(client, directory)

            envelope = await application.execute_command(
                "import",
                {"parent": "viking://demo"},
                request_id="failed-upload",
                upload=FakeUpload(filename="pack.ovpack", content_type="application/octet-stream"),
            )

            self.assertFalse(envelope.ok)
            self.assertEqual(envelope.error.code, "OPENVIKING_INVALID_INPUT")
            staged_path = Path(client.calls[0][1]["file_path"])
            self.assertEqual(staged_path.suffix, ".ovpack")
            self.assertFalse(staged_path.exists())

    async def test_long_running_command_returns_a_job_before_blocking_work_finishes(self):
        client = BlockingCommandClient(result={"uri": "viking://demo/backup"})
        with temporary_directory() as directory:
            application = make_application(client, directory)

            envelope = await application.execute_command(
                "backup",
                {"to": "viking://demo/backup"},
                request_id="backup-request",
            )

            self.assertTrue(envelope.ok)
            job = envelope.data["job"]
            self.assertIn(job["status"], {"queued", "running"})
            self.assertEqual(job["request_id"], "backup-request")
            self.assertEqual(job["command"], "backup")
            await client.started.wait()
            self.assertEqual((await application.get_job(job["job_id"]))["status"], "running")

            client.release.set()
            completed = await wait_for_job(application, job["job_id"], "succeeded")
            self.assertEqual(completed["result"], {"uri": "viking://demo/backup"})
            self.assertIsNone(completed["error"])

    async def test_native_command_without_a_wait_parameter_keeps_its_sdk_signature(self):
        client = FakeCommandClient(result={"task_id": "set-tags-task"})
        with temporary_directory() as directory:
            application = make_application(client, directory)

            envelope = await application.execute_command(
                "set-tags",
                {"uri": "viking://demo", "tags": ["demo"]},
                request_id="set-tags-request",
            )

            self.assertTrue(envelope.ok)
            self.assertEqual(
                client.calls,
                [("set_tags", {"uri": "viking://demo", "tags": ["demo"]})],
            )

    async def test_blocking_wait_command_also_enters_the_job_registry(self):
        client = FakeCommandClient(result={"processed": True})
        with temporary_directory() as directory:
            application = make_application(client, directory)

            envelope = await application.execute_command(
                "system wait", {"timeout": 0.1}, request_id="wait-request"
            )

            self.assertTrue(envelope.ok)
            self.assertEqual(envelope.data["job"]["command"], "system wait")
            self.assertIn(envelope.data["job"]["status"], {"queued", "running"})
            completed = await wait_for_job(
                application, envelope.data["job"]["job_id"], "succeeded"
            )
            self.assertEqual(completed["result"], {"processed": True})

    async def test_failed_background_job_records_structured_error(self):
        class FailingBlockingClient(BlockingCommandClient):
            async def call(self, method, arguments):
                self.calls.append((method, dict(arguments)))
                self.started.set()
                await self.release.wait()
                raise OpenVikingError("background failed", code="UNAVAILABLE")

        client = FailingBlockingClient()
        with temporary_directory() as directory:
            application = make_application(client, directory)
            envelope = await application.execute_command(
                "backup",
                {"to": "viking://demo/backup"},
                request_id="failed-background",
            )
            job_id = envelope.data["job"]["job_id"]
            await client.started.wait()

            client.release.set()
            failed = await wait_for_job(application, job_id, "failed")

            self.assertIsNone(failed["result"])
            self.assertEqual(failed["error"]["code"], "OPENVIKING_UNAVAILABLE")
            self.assertEqual(failed["error"]["message"], "background failed")

    async def test_restart_marks_running_local_jobs_interrupted(self):
        client = BlockingCommandClient()
        with temporary_directory() as directory:
            first = make_application(client, directory)
            envelope = await first.execute_command(
                "backup", {"to": "viking://demo/backup"}, request_id="restart-request"
            )
            job_id = envelope.data["job"]["job_id"]
            await client.started.wait()

            second = make_application(FakeCommandClient(), directory)
            interrupted = await second.get_job(job_id)

            self.assertEqual(interrupted["status"], "interrupted")
            self.assertEqual(interrupted["error"]["code"], "JOB_INTERRUPTED")

            client.release.set()
            await wait_for_job(first, job_id, "succeeded")

    async def test_restarted_native_job_queries_authoritative_openviking_task(self):
        native_result = {"task_id": "native-restart-task"}
        with temporary_directory() as directory:
            first = make_application(
                FakeCommandClient(result=native_result), directory
            )
            envelope = await first.execute_command(
                "add-resource", {"path": "D:/existing/path"}, request_id="native-request"
            )
            job_id = envelope.data["job"]["job_id"]

            second_client = FakeCommandClient(
                result={"status": "succeeded", "result": {"uri": "viking://demo"}}
            )
            second = make_application(second_client, directory)
            job = await second.get_job(job_id)

            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["openviking_task_id"], "native-restart-task")
            self.assertEqual(job["result"], {"uri": "viking://demo"})
            self.assertEqual(
                second_client.calls,
                [("get_task", {"task_id": "native-restart-task"})],
            )
    async def test_native_task_failure_persists_a_structured_error(self):
        with temporary_directory() as directory:
            first = make_application(
                FakeCommandClient(result={"task_id": "native-fail-task"}), directory
            )
            envelope = await first.execute_command(
                "add-resource", {"path": "D:/existing/path"}, request_id="native-fail"
            )
            job_id = envelope.data["job"]["job_id"]
            self.assertEqual(envelope.data["job"]["status"], "running")

            second = make_application(
                FakeCommandClient(
                    result={
                        "status": "failed",
                        "error": {"code": "INVALID", "message": "provider rejected"},
                    }
                ),
                directory,
            )
            job = await second.get_job(job_id)

            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["openviking_task_id"], "native-fail-task")
            self.assertIsNone(job["result"])
            self.assertEqual(job["error"]["code"], "OPENVIKING_INVALID")
            self.assertEqual(job["error"]["message"], "provider rejected")

            offline = make_application(
                FakeCommandClient(
                    error=OpenVikingError("down", code="UNAVAILABLE")
                ),
                directory,
            )
            stored = await offline.get_job(job_id)
            self.assertEqual(stored["status"], "failed")
            self.assertEqual(stored["error"]["code"], "OPENVIKING_INVALID")
            self.assertEqual(stored["error"]["message"], "provider rejected")

    async def test_native_task_success_after_failure_clears_the_stored_error(self):
        with temporary_directory() as directory:
            first = make_application(
                FakeCommandClient(result={"task_id": "native-recover-task"}), directory
            )
            envelope = await first.execute_command(
                "add-resource", {"path": "D:/existing/path"}
            )
            job_id = envelope.data["job"]["job_id"]

            failing = make_application(
                FakeCommandClient(
                    result={
                        "status": "failed",
                        "error": {"code": "INTERNAL", "message": "boom"},
                    }
                ),
                directory,
            )
            failed = await failing.get_job(job_id)
            self.assertEqual(failed["status"], "failed")

            succeeding = make_application(
                FakeCommandClient(
                    result={
                        "status": "succeeded",
                        "result": {"uri": "viking://demo/done"},
                    }
                ),
                directory,
            )
            job = await succeeding.get_job(job_id)

            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["openviking_task_id"], "native-recover-task")
            self.assertEqual(job["result"], {"uri": "viking://demo/done"})
            self.assertIsNone(job["error"])

            offline = make_application(
                FakeCommandClient(
                    error=OpenVikingError("down", code="UNAVAILABLE")
                ),
                directory,
            )
            stored = await offline.get_job(job_id)
            self.assertEqual(stored["status"], "succeeded")
            self.assertEqual(stored["result"], {"uri": "viking://demo/done"})
            self.assertIsNone(stored["error"])


class FileUploadAndJobEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_long_running_command_returns_before_work_finishes(self):
        client = BlockingCommandClient(result={"uri": "viking://demo/export"})
        with temporary_directory() as directory:
            application = make_application(client, directory)
            transport = httpx.ASGITransport(app=create_app(application=application))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:
                response = await http.post(
                    "/commands/execute",
                    json={
                        "command": "export",
                        "arguments": {
                            "uri": "viking://demo",
                            "to": "viking://demo/export",
                        },
                        "request_id": "http-long-running",
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                envelope = response.json()
                job = envelope["data"]["job"]
                self.assertIn(job["status"], {"queued", "running"})
                await client.started.wait()
                status = await http.get(f"/jobs/{job['job_id']}")
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["data"]["status"], "running")

                client.release.set()
                for _ in range(100):
                    completed = (await http.get(f"/jobs/{job['job_id']}")).json()["data"]
                    if completed["status"] == "succeeded":
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(completed["result"], {"uri": "viking://demo/export"})

    async def test_multipart_command_and_job_status_use_the_rest_surface(self):
        client = FakeCommandClient(result={"task_id": "rest-upload-task"})
        with temporary_directory() as directory:
            application = make_application(client, directory)
            transport = httpx.ASGITransport(app=create_app(application=application))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:
                response = await http.post(
                    "/commands/execute",
                    data={
                        "command": "add-skill",
                        "arguments_json": '{"options": {"target_uri": "viking://demo"}}',
                        "request_id": "rest-upload-request",
                    },
                    files={
                        "file": (
                            "skill.md",
                            b"# Skill\n",
                            "text/markdown",
                        )
                    },
                )

                self.assertEqual(response.status_code, 200, response.text)
                envelope = response.json()
                self.assertTrue(envelope["ok"])
                job = envelope["data"]["job"]
                self.assertEqual(job["openviking_task_id"], "rest-upload-task")
                self.assertEqual(client.calls[0][0], "add_skill")
                self.assertFalse(client.calls[0][1]["wait"])

                status = await http.get(f"/jobs/{job['job_id']}")
                self.assertEqual(status.status_code, 200, status.text)
                status_envelope = status.json()
                self.assertTrue(status_envelope["ok"])
                self.assertEqual(status_envelope["data"]["job_id"], job["job_id"])
                self.assertEqual(
                    status_envelope["data"]["openviking_task_id"],
                    "rest-upload-task",
                )
    async def test_multipart_malformed_arguments_json_returns_validation_error_without_executing(self):
        client = FakeCommandClient()
        with temporary_directory() as directory:
            application = make_application(client, directory)
            transport = httpx.ASGITransport(app=create_app(application=application))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:
                for raw in ("not-json", '["not", "an", "object"]'):
                    with self.subTest(arguments_json=raw):
                        response = await http.post(
                            "/commands/execute",
                            data={
                                "command": "add-skill",
                                "arguments_json": raw,
                                "request_id": "bad-arguments",
                            },
                            files={"file": ("skill.md", b"# Skill\n", "text/markdown")},
                        )

                        self.assertEqual(response.status_code, 400, response.text)
                        envelope = response.json()
                        self.assertFalse(envelope["ok"])
                        self.assertIsNone(envelope["data"])
                        self.assertEqual(envelope["command"], "add-skill")
                        self.assertEqual(envelope["request_id"], "bad-arguments")
                        self.assertEqual(envelope["error"]["code"], "VALIDATION_ERROR")
            self.assertEqual(client.calls, [])

    async def test_multipart_malformed_arguments_json_fails_without_required_arguments(self):
        client = FakeCommandClient()
        with temporary_directory() as directory:
            application = make_application(client, directory)
            transport = httpx.ASGITransport(app=create_app(application=application))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as http:
                response = await http.post(
                    "/commands/execute",
                    data={"command": "status", "arguments_json": "{oops"},
                    files={"file": ("notes.md", b"# Notes\n", "text/markdown")},
                )

                self.assertEqual(response.status_code, 400, response.text)
                envelope = response.json()
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["command"], "status")
                self.assertEqual(envelope["error"]["code"], "VALIDATION_ERROR")
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()

