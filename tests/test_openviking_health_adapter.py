import unittest
from pathlib import Path

import httpx

from agentic_rag.config import Settings
from agentic_rag.health import DependencyStatus
from agentic_rag.openviking import HttpOpenVikingHealthAdapter


def make_settings():
    return Settings.from_env(environ={}, env_file=Path("does-not-exist.env"))


class HttpOpenVikingHealthAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def check_with_handler(self, handler):
        settings = make_settings()
        client = httpx.AsyncClient(
            base_url=str(settings.openviking_base_url),
            transport=httpx.MockTransport(handler),
        )
        adapter = HttpOpenVikingHealthAdapter(settings=settings, client=client)
        try:
            return await adapter.check()
        finally:
            await client.aclose()

    async def test_success_is_reachable(self):
        async def handler(request):
            return httpx.Response(200, json={"status": "ok"})

        result = await self.check_with_handler(handler)
        self.assertEqual(result.status, DependencyStatus.UP)
        self.assertTrue(result.reachable)

    async def test_http_failure_is_dependency_down(self):
        async def handler(request):
            return httpx.Response(503, text="unavailable")

        result = await self.check_with_handler(handler)
        self.assertEqual(result.status, DependencyStatus.DOWN)
        self.assertFalse(result.reachable)
        self.assertEqual(result.detail, "OpenViking Server returned HTTP 503")

    async def test_transport_failure_does_not_leak_error_text(self):
        async def handler(request):
            raise httpx.ConnectError("credential-in-error")

        result = await self.check_with_handler(handler)
        self.assertEqual(result.status, DependencyStatus.DOWN)
        self.assertEqual(result.detail, "OpenViking Server is unreachable")
        self.assertNotIn("credential-in-error", result.detail)


if __name__ == "__main__":
    unittest.main()
