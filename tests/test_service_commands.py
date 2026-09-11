import unittest
from pathlib import Path

import httpx

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import Settings
from agentic_rag.health import DependencyHealth, DependencyStatus
from agentic_rag.service import create_app


class FakeOpenVikingHealthAdapter:
    async def check(self):
        return DependencyHealth(status=DependencyStatus.UP, reachable=True)


class FakeCommandClient:
    def __init__(self, result=None):
        self.result = result

    async def call(self, method, arguments):
        return self.result


def make_application():
    settings = Settings.from_env(environ={}, env_file=Path("does-not-exist.env"))
    return AgenticRagApplication(
        settings=settings,
        health_adapter=FakeOpenVikingHealthAdapter(),
        command_client=FakeCommandClient(result={"items": []}),
    )


class CommandEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, application, method, path, json=None):
        transport = httpx.ASGITransport(app=create_app(application=application))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, json=json)

    async def test_catalog_endpoint_exposes_generated_manifest(self):
        response = await self.request(make_application(), "GET", "/commands")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "openviking-cli")
        names = {command["name"] for command in body["commands"]}
        self.assertIn("find", names)
        self.assertNotIn("tui", names)

    async def test_generic_execution_endpoint_uses_application_and_envelope(self):
        application = make_application()
        response = await self.request(
            application,
            "POST",
            "/commands/execute",
            json={
                "command": "find",
                "arguments": {"query": "知识库"},
                "request_id": "request-rest",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "data": {"items": []},
                "command": "find",
                "request_id": "request-rest",
                "error": None,
            },
        )


    async def test_malformed_request_body_still_uses_command_envelope(self):
        response = await self.request(make_application(), "POST", "/commands/execute")

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertIsNone(body["data"])
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
        self.assertTrue(body["request_id"])

    async def test_validation_failure_returns_envelope_and_400(self):
        response = await self.request(
            make_application(),
            "POST",
            "/commands/execute",
            json={"command": "mkdir", "arguments": {}, "request_id": "invalid"},
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["request_id"], "invalid")
        self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
