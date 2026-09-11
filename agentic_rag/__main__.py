"""Run the Agentic RAG REST service or REPL."""

from __future__ import annotations

import argparse

import uvicorn

from .config import Settings
from .service import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentic-rag")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the REST service (default)")
    serve.add_argument("--host", help="Bind address; defaults to 127.0.0.1")
    serve.add_argument("--port", type=int, help="Bind port; defaults to 8000")

    subparsers.add_parser("repl", help="Run the interactive REPL")

    arguments = parser.parse_args(argv)
    if arguments.command == "repl":
        from .repl import main as repl_main

        repl_main()
        return

    settings = Settings.from_env()
    app = create_app(settings=settings)
    uvicorn.run(
        app,
        host=getattr(arguments, "host", None) or settings.host,
        port=getattr(arguments, "port", None) or settings.port,
    )


if __name__ == "__main__":
    main()
