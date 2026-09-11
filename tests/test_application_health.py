import inspect
import tempfile
import unittest
from pathlib import Path

import httpx

from agentic_rag.application import AgenticRagApplication
from agentic_rag.config import ConfigurationError, Settings
from agentic_rag.health import DependencyHealth, DependencyStatus, HealthStatus
from agentic_rag.service import create_app


class FakeOpenVikingHealthAdapter:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def check(self):
        if self.error is not None:
            raise self.error
        return self.result


def make_application(adapter, environ=None):
    settings = Settings.from_env(
        environ=environ or {},
        env_file=Path("does-not-exist.env"),
    )
    return AgenticRagApplication(settings=settings, health_adapter=adapter)


class SettingsTests(unittest.TestCase):
    def test_defaults_bind_loopback_and_keep_secrets_out_of_safe_output(self):
        settings = Settings.from_env(
            environ={
                "DEEPSEEK_API_KEY": "model-secret",
                "OPENVIKING_API_KEY": "openviking-secret",
            },
            env_file=Path("does-not-exist.env"),
        )

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8000)
        self.assertNotIn("model-secret", repr(settings))
        self.assertNotIn("openviking-secret", repr(settings))
        self.assertEqual(
            settings.safe_summary(),
            {
                "host": "127.0.0.1",
                "port": 8000,
                "credentials": {
                    "DEEPSEEK_API_KEY": True,
                    "OPENVIKING_API_KEY": True,
                },
            },
        )

    def test_environment_takes_precedence_over_local_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "settings.env"
            env_path.write_text("HOST=0.0.0.0\nPORT=9001\n", encoding="utf-8")
            settings = Settings.from_env(
                environ={"HOST": "127.0.0.1"},
                env_file=env_path,
            )

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 9001)

    def test_blank_credentials_count_as_missing(self):
        settings = Settings.from_env(
            environ={
                "DEEPSEEK_API_KEY": "",
                "OPENVIKING_API_KEY": " ",
            },
            env_file=Path("does-not-exist.env"),
        )

        self.assertEqual(
            settings.safe_summary()["credentials"],
            {
                "DEEPSEEK_API_KEY": False,
                "OPENVIKING_API_KEY": False,
            },
        )

    def test_invalid_configuration_names_setting_without_value(self):
        with self.assertRaises(ConfigurationError) as raised:
            Settings.from_env(
                environ={"PORT": "not-a-port"},
                env_file=Path("does-not-exist.env"),
            )

        self.assertEqual(str(raised.exception), "Invalid setting: PORT")
        self.assertIsNone(raised.exception.__cause__)


class ApplicationSeamTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_path_names_missing_credential_without_secret(self):
        application = make_application(FakeOpenVikingHealthAdapter())

        stream = application.stream_conversation(
            session_id=None,
            message="知识库里有什么？",
        )
        events = [event async for event in stream]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["code"], "CONFIGURATION_ERROR")
        self.assertEqual(events[0]["message"], "Missing setting: DEEPSEEK_API_KEY")
        self.assertTrue(events[0]["request_id"])
        self.assertTrue(events[0]["session_id"])
        self.assertTrue(events[0]["timestamp"])

    async def test_blank_model_credential_is_reported_as_missing(self):
        application = make_application(
            FakeOpenVikingHealthAdapter(),
            environ={"DEEPSEEK_API_KEY": ""},
        )

        stream = application.stream_conversation(
            session_id=None,
            message="知识库里有什么？",
        )
        events = [event async for event in stream]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["code"], "CONFIGURATION_ERROR")
        self.assertEqual(events[0]["message"], "Missing setting: DEEPSEEK_API_KEY")

    def test_application_exposes_the_single_later_ticket_boundary(self):
        application = make_application(FakeOpenVikingHealthAdapter())

        for method_name in (
            "stream_conversation",
            "execute_command",
            "list_sessions",
            "get_session",
            "delete_session",
            "get_job",
            "health",
        ):
            with self.subTest(method_name=method_name):
                self.assertTrue(inspect.ismethod(getattr(application, method_name)))


class HealthBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_health_separates_application_and_dependency(self):
        application = make_application(
            FakeOpenVikingHealthAdapter(
                DependencyHealth(status=DependencyStatus.UP, reachable=True)
            )
        )

        health = await application.health()

        self.assertEqual(health.status, HealthStatus.OK)
        self.assertEqual(health.application.status, "ready")
        self.assertEqual(
            health.dependencies["openviking_server"].status,
            DependencyStatus.UP,
        )
        self.assertTrue(health.dependencies["openviking_server"].reachable)

    async def test_dependency_failure_does_not_crash_application_health(self):
        application = make_application(
            FakeOpenVikingHealthAdapter(
                DependencyHealth(
                    status=DependencyStatus.DOWN,
                    reachable=False,
                    detail="OpenViking Server is unreachable",
                )
            )
        )

        health = await application.health()

        self.assertEqual(health.status, HealthStatus.DEGRADED)
        self.assertEqual(health.application.status, "ready")
        self.assertEqual(
            health.dependencies["openviking_server"].status,
            DependencyStatus.DOWN,
        )

    async def test_dependency_details_are_redacted_before_reporting(self):
        application = make_application(
            FakeOpenVikingHealthAdapter(
                DependencyHealth(
                    status=DependencyStatus.DOWN,
                    reachable=False,
                    detail="failed with model-secret and openviking-secret",
                )
            ),
            environ={
                "DEEPSEEK_API_KEY": "model-secret",
                "OPENVIKING_API_KEY": "openviking-secret",
            },
        )

        health = await application.health()
        payload = health.as_dict()

        self.assertNotIn("model-secret", str(payload))
        self.assertNotIn("openviking-secret", str(payload))
        self.assertIn("[redacted]", payload["dependencies"]["openviking_server"]["detail"])

    async def test_dependency_configuration_error_reports_setting_only(self):
        application = make_application(
            FakeOpenVikingHealthAdapter(
                error=ConfigurationError(("OPENVIKING_API_KEY",))
            )
        )

        health = await application.health()

        self.assertEqual(health.status, HealthStatus.DEGRADED)
        self.assertEqual(
            health.dependencies["openviking_server"].status,
            DependencyStatus.CONFIGURATION_ERROR,
        )
        self.assertEqual(
            health.dependencies["openviking_server"].detail,
            "Missing setting: OPENVIKING_API_KEY",
        )


class ServiceHealthEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def request_health(self, application):
        fastapi_app = create_app(application=application)
        transport = httpx.ASGITransport(app=fastapi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health")

    def test_application_and_settings_cannot_both_control_composition(self):
        application = make_application(FakeOpenVikingHealthAdapter())
        settings = Settings.from_env(
            environ={},
            env_file=Path("does-not-exist.env"),
        )

        with self.assertRaises(ValueError):
            create_app(application=application, settings=settings)

    async def test_endpoint_reports_success_and_dependency_failure_separately(self):
        healthy = make_application(
            FakeOpenVikingHealthAdapter(
                DependencyHealth(status=DependencyStatus.UP, reachable=True)
            )
        )
        response = await self.request_health(healthy)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(
            response.json()["dependencies"]["openviking_server"]["status"],
            "up",
        )

        unhealthy = make_application(
            FakeOpenVikingHealthAdapter(
                DependencyHealth(
                    status=DependencyStatus.DOWN,
                    reachable=False,
                    detail="OpenViking Server is unreachable",
                )
            )
        )
        response = await self.request_health(unhealthy)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["application"]["status"], "ready")
        self.assertEqual(
            response.json()["dependencies"]["openviking_server"]["status"],
            "down",
        )


if __name__ == "__main__":
    unittest.main()








