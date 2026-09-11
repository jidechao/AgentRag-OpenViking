import unittest
from pathlib import Path
from unittest import mock

from agentic_rag.__main__ import main
from agentic_rag.config import Settings


class EntrypointTests(unittest.TestCase):
    def test_default_startup_uses_loopback_and_configured_port(self):
        settings = Settings.from_env(
            environ={},
            env_file=Path("does-not-exist.env"),
        )
        with (
            mock.patch("agentic_rag.__main__.Settings.from_env", return_value=settings),
            mock.patch("agentic_rag.__main__.uvicorn.run") as run,
        ):
            main([])

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 8000)

    def test_repl_subcommand_dispatches_to_repl_entrypoint(self):
        with mock.patch("agentic_rag.repl.main") as repl_main:
            main(["repl"])

        self.assertEqual(repl_main.call_count, 1)


if __name__ == "__main__":
    unittest.main()


