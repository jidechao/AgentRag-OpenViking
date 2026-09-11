import asyncio
import time
import unittest
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.openviking import SdkOpenVikingCommandClient


class FakeSdkClient:
    def __init__(self):
        self.calls = []
        self.initialize_count = 0

    def sync_status(self):
        self.calls.append({"method": "sync_status"})
        return {"sync": True}

    async def initialize(self):
        self.initialize_count += 1
        self.calls.append({"__initialize__": True})

    async def _get_system_status(self):
        self.calls.append({"method": "_get_system_status"})
        return {"healthy": True}

    def get_status(self):
        time.sleep(0.4)
        raise AssertionError("the blocking SDK wrapper must not be used")

    async def find(self, **arguments):
        self.calls.append(arguments)
        return {"items": []}


def make_settings():
    return Settings.from_env(environ={}, env_file=Path("does-not-exist.env"))


class SdkOpenVikingCommandClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_synchronous_sdk_method_from_async_adapter(self):
        sdk_client = FakeSdkClient()
        adapter = SdkOpenVikingCommandClient(
            settings=make_settings(),
            client=sdk_client,
        )

        result = await adapter.call("sync_status", {})

        self.assertEqual(result, {"sync": True})
        self.assertEqual(sdk_client.calls, [{"__initialize__": True}, {"method": "sync_status"}])

    async def test_status_call_does_not_block_on_the_installed_sdk_wrapper(self):
        sdk_client = FakeSdkClient()
        adapter = SdkOpenVikingCommandClient(
            settings=make_settings(),
            client=sdk_client,
        )

        result = await asyncio.wait_for(adapter.call("get_status", {}), timeout=0.2)

        self.assertEqual(result, {"healthy": True})

    async def test_calls_async_method_on_injected_sdk_client_without_cli(self):
        sdk_client = FakeSdkClient()
        adapter = SdkOpenVikingCommandClient(
            settings=make_settings(),
            client=sdk_client,
        )

        result = await adapter.call("find", {"query": "知识库", "limit": 2})

        self.assertEqual(result, {"items": []})
        self.assertEqual(
            sdk_client.calls,
            [{"__initialize__": True}, {"query": "知识库", "limit": 2}],
        )

        await adapter.call("find", {"query": "第二次", "limit": 1})

        self.assertEqual(sdk_client.initialize_count, 1)

    async def test_concurrent_calls_initialize_the_sdk_only_once(self):
        class SlowInitializeSdkClient(FakeSdkClient):
            async def initialize(self):
                self.initialize_count += 1
                await asyncio.sleep(0.05)
                self.calls.append({"__initialize__": True})

        sdk_client = SlowInitializeSdkClient()
        adapter = SdkOpenVikingCommandClient(
            settings=make_settings(),
            client=sdk_client,
        )

        results = await asyncio.gather(
            *(adapter.call("sync_status", {}) for _ in range(8))
        )

        self.assertEqual(results, [{"sync": True}] * 8)
        self.assertEqual(sdk_client.initialize_count, 1)


if __name__ == "__main__":
    unittest.main()
