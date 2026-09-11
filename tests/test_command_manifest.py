import unittest
from pathlib import Path

from agentic_rag.commands import CommandCatalog

CATALOG = CommandCatalog.load()


class CommandManifestTests(unittest.TestCase):
    def test_catalog_exposes_cli_semantic_commands_not_local_or_internal_surface(self):
        names = set(CATALOG.names)

        self.assertIn("find", names)
        self.assertIn("attrs get", names)
        self.assertIn("attrs set-tags", names)
        self.assertNotIn("attrs", names)
        self.assertIn("skills list", names)
        self.assertIn("task list", names)
        self.assertIn("admin delete-account", names)
        self.assertIn("privacy get", names)
        self.assertNotIn("tui", names)
        self.assertNotIn("config", names)
        self.assertNotIn("language", names)
        self.assertNotIn("version", names)
        self.assertNotIn("acl_get", names)
        self.assertNotIn("GET /api/v1/files", names)

    def test_sdk_command_metadata_and_signature_schema_are_generated(self):
        command = CATALOG.get("mkdir")

        self.assertEqual(command.transport, "sdk")
        self.assertEqual(command.sdk_method, "mkdir")
        self.assertEqual(command.asynchronous, "sync")
        self.assertEqual(command.file_input, "none")
        self.assertEqual(command.danger, "mutating")
        self.assertEqual(
            command.input_schema,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "description": {"type": ["string", "null"], "default": None},
                },
                "required": ["uri"],
                "additionalProperties": False,
            },
        )

    def test_sdk_signature_gap_stays_on_rest_fallback_transport(self):
        attrs_get = CATALOG.get("attrs get")

        self.assertEqual(attrs_get.transport, "rest_fallback")
        self.assertIsNone(attrs_get.sdk_method)

    def test_union_signatures_accept_each_json_type(self):
        target_uri = CATALOG.get("find").input_schema["properties"]["target_uri"]

        self.assertEqual(
            target_uri,
            {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "default": "",
            },
        )

    def test_every_sdk_entry_targets_an_installed_async_sdk_method(self):
        from openviking_sdk.client import AsyncHTTPClient

        for command in CATALOG.commands:
            with self.subTest(command=command.name):
                self.assertIn(command.transport, {"sdk", "rest_fallback"})
                self.assertIn(command.asynchronous, {"sync", "native_async", "blocking_wait", "long_running"})
                self.assertIn(command.file_input, {"none", "input_optional", "input_required", "output"})
                self.assertIn(command.danger, {"read_only", "mutating", "destructive", "privileged"})
                if command.transport == "sdk":
                    self.assertTrue(hasattr(AsyncHTTPClient, command.sdk_method))
                    self.assertEqual(command.input_schema["type"], "object")
                    self.assertIs(command.input_schema["additionalProperties"], False)

    def test_representative_command_families_are_present(self):
        for name in (
            "read",
            "find",
            "mkdir",
            "skills list",
            "task list",
            "admin delete-account",
        ):
            with self.subTest(command=name):
                self.assertIsNotNone(CATALOG.get(name))


if __name__ == "__main__":
    unittest.main()
