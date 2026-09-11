import unittest
import uuid
from pathlib import Path

from openviking_sdk.errors import OpenVikingError

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus


class FakeOpenVikingHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeCommandClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def call(self, method, arguments):
        self.calls.append((method, dict(arguments)))
        if self.error is not None:
            raise self.error
        return self.result


def make_application(command_client, environ=None):
    settings = Settings.from_env(
        environ=environ or {},
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeOpenVikingHealthAdapter(),
        command_client=command_client,
    )


class CommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_command_returns_unified_success_envelope(self):
        client = FakeCommandClient(result={"uri": "viking://demo", "content": "答案"})
        application = make_application(client)

        envelope = await application.execute_command(
            "read",
            {"uri": "viking://demo", "offset": 2, "limit": 10},
            request_id="request-1",
        )

        self.assertEqual(
            envelope.as_dict(),
            {
                "ok": True,
                "data": {"uri": "viking://demo", "content": "答案"},
                "command": "read",
                "request_id": "request-1",
                "error": None,
            },
        )
        self.assertEqual(client.calls, [("read", {"uri": "viking://demo", "offset": 2, "limit": 10})])

    async def test_binary_sdk_result_is_json_safe(self):
        client = FakeCommandClient(result=b"binary")
        application = make_application(client)

        envelope = await application.execute_command(
            "get", {"uri": "viking://demo"}, request_id="binary"
        )

        self.assertEqual(envelope.data, {"encoding": "base64", "data": "YmluYXJ5"})

    async def test_representative_command_families_execute_by_sdk_method(self):
        client = FakeCommandClient(result={"items": []})
        application = make_application(client)

        cases = {
            "find": ("find", {"query": "知识库"}),
            "mkdir": ("mkdir", {"uri": "viking://demo"}),
            "skills list": ("list_skills", {"node_limit": 5}),
            "task list": ("list_tasks", {"status": "running"}),
            "admin delete-account": ("admin_delete_account", {"account_id": "demo"}),
        }
        for command_name, (method, arguments) in cases.items():
            with self.subTest(command=command_name):
                await application.execute_command(command_name, arguments, request_id=f"r-{command_name}")

        self.assertEqual(
            [call[0] for call in client.calls],
            ["find", "mkdir", "list_skills", "list_tasks", "admin_delete_account"],
        )

    async def test_missing_unknown_and_wrong_type_arguments_are_structured(self):
        application = make_application(FakeCommandClient())

        missing = await application.execute_command("mkdir", {}, request_id="missing")
        unknown = await application.execute_command(
            "mkdir", {"uri": "viking://demo", "recursive": True}, request_id="unknown"
        )
        wrong_type = await application.execute_command(
            "mkdir", {"uri": 42}, request_id="wrong-type"
        )
        nested_type = await application.execute_command(
            "find", {"target_uri": [1]}, request_id="nested-type"
        )

        for envelope, request_id, expected_path in (
            (missing, "missing", "uri"),
            (unknown, "unknown", "recursive"),
            (wrong_type, "wrong-type", "uri"),
            (nested_type, "nested-type", "target_uri"),
        ):
            with self.subTest(request_id=request_id):
                self.assertFalse(envelope.ok)
                self.assertIsNone(envelope.data)
                self.assertEqual(envelope.request_id, request_id)
                self.assertEqual(envelope.error.code, "VALIDATION_ERROR")
                self.assertEqual(envelope.error.details["errors"][0]["path"], expected_path)

    async def test_unknown_command_and_missing_rest_adapter_do_not_reach_sdk(self):
        client = FakeCommandClient()
        application = make_application(client)

        unknown = await application.execute_command("debug", {}, request_id="unknown")
        fallback = await application.execute_command(
            "cp",
            {"from_uri": "viking://a", "to_uri": "viking://b"},
            request_id="fallback",
        )

        self.assertEqual(unknown.error.code, "UNKNOWN_COMMAND")
        self.assertEqual(fallback.error.code, "REST_CLIENT_UNCONFIGURED")
        self.assertEqual(client.calls, [])

    async def test_openviking_error_is_translated_and_credentials_are_redacted(self):
        secret = "openviking-secret"
        client = FakeCommandClient(
            error=OpenVikingError(
                f"request failed for {secret}",
                code="UNAVAILABLE",
                details={"authorization": secret},
            )
        )
        application = make_application(client, environ={"OPENVIKING_API_KEY": secret})

        envelope = await application.execute_command("find", {"query": "demo"}, request_id="error")

        self.assertFalse(envelope.ok)
        self.assertEqual(envelope.error.code, "OPENVIKING_UNAVAILABLE")
        self.assertEqual(envelope.error.message, "request failed for [redacted]")
        self.assertEqual(envelope.error.details, {"authorization": "[redacted]"})
        self.assertNotIn(secret, str(envelope.as_dict()))

    async def test_unexpected_error_does_not_leak_provider_details(self):
        client = FakeCommandClient(error=RuntimeError("stack includes model-secret"))
        application = make_application(client)

        envelope = await application.execute_command("find", {"query": "demo"}, request_id="internal")

        self.assertFalse(envelope.ok)
        self.assertEqual(envelope.error.code, "INTERNAL_ERROR")
        self.assertEqual(envelope.error.message, "OpenViking command execution failed")
        self.assertNotIn("model-secret", str(envelope.as_dict()))

    async def test_request_id_is_generated_when_omitted(self):
        application = make_application(FakeCommandClient(result={}))

        envelope = await application.execute_command("status")

        self.assertIsNotNone(envelope.request_id)
        uuid.UUID(envelope.request_id)


if __name__ == "__main__":
    unittest.main()
