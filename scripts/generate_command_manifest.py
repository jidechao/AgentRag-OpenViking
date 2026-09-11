"""Generate the OpenViking CLI Semantic Command manifest.

The command names and descriptions come from the installed OpenViking CLI. SDK-backed
input schemas are derived from ``AsyncHTTPClient`` method signatures, so no per-command
REST route or handwritten parameter table is needed.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
import sys
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent
from typing import Any

from openviking_sdk.client import AsyncHTTPClient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "agentic_rag" / "command_manifest.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_rag.commands import openapi_operation_schema  # noqa: E402

Metadata = tuple[str, str, str, str]
Command = tuple[str, str | None, str, str, str, str]

SYNC = "sync"
NATIVE = "native_async"
WAIT = "blocking_wait"
LONG = "long_running"
NONE = "none"
INPUT_OPTIONAL = "input_optional"
INPUT_REQUIRED = "input_required"
FILE_OUTPUT = "output"
READ = "read_only"
MUTATING = "mutating"
DESTRUCTIVE = "destructive"
PRIVILEGED = "privileged"

COMMANDS: list[Command] = [
    ("health", "health", SYNC, NONE, READ),
    ("status", "get_status", SYNC, NONE, READ),
    ("find", "find", SYNC, NONE, READ),
    ("read", "read", SYNC, NONE, READ),
    ("write", "write", NATIVE, NONE, MUTATING),
    ("add-resource", "add_resource", NATIVE, INPUT_OPTIONAL, MUTATING),
    ("add-skill", "add_skill", NATIVE, INPUT_OPTIONAL, MUTATING),
    ("add-memory", None, NATIVE, NONE, MUTATING),
    ("set-tags", "set_tags", NATIVE, NONE, MUTATING),
    ("ls", "ls", SYNC, NONE, READ),
    ("tree", "tree", SYNC, NONE, READ),
    ("mkdir", "mkdir", SYNC, NONE, MUTATING),
    ("rm", "rm", NATIVE, NONE, DESTRUCTIVE),
    ("cp", None, SYNC, NONE, MUTATING),
    ("mv", "mv", SYNC, NONE, MUTATING),
    ("stat", "stat", SYNC, NONE, READ),
    ("attrs get", None, SYNC, NONE, READ),
    ("attrs set-tags", "set_tags", NATIVE, NONE, MUTATING),
    ("get", "download_bytes", SYNC, FILE_OUTPUT, READ),
    ("search", "search", SYNC, NONE, READ),
    ("grep", "grep", SYNC, NONE, READ),
    ("glob", "glob", SYNC, NONE, READ),
    ("abstract", None, SYNC, NONE, READ),
    ("overview", "overview", SYNC, NONE, READ),
    ("wait", "wait_processed", WAIT, NONE, READ),
    ("reindex", "reindex", NATIVE, NONE, MUTATING),
    ("import", "import_ovpack", NATIVE, INPUT_REQUIRED, MUTATING),
    ("export", "export_ovpack", LONG, FILE_OUTPUT, READ),
    ("backup", "backup_ovpack", LONG, FILE_OUTPUT, READ),
    ("restore", "restore_ovpack", NATIVE, INPUT_REQUIRED, MUTATING),
    ("chat", None, SYNC, NONE, MUTATING),
    ("compile", None, NATIVE, NONE, MUTATING),
    ("skills add", "add_skill", NATIVE, INPUT_OPTIONAL, MUTATING),
    ("skills list", "list_skills", SYNC, NONE, READ),
    ("skills find", "find_skills", SYNC, NONE, READ),
    ("skills show", "get_skill", SYNC, NONE, READ),
    ("skills update", "update_skill", NATIVE, INPUT_OPTIONAL, MUTATING),
    ("skills remove", "delete_skill", SYNC, NONE, DESTRUCTIVE),
    ("skills validate", "validate_skill", SYNC, INPUT_OPTIONAL, READ),
    ("acl get", "acl_get", SYNC, NONE, READ),
    ("acl set", "acl_set", SYNC, NONE, MUTATING),
    ("acl grant", "acl_grant", SYNC, NONE, MUTATING),
    ("acl revoke", "acl_revoke", SYNC, NONE, MUTATING),
    ("acl rm", "acl_delete", SYNC, NONE, DESTRUCTIVE),
    ("task status", "get_task", SYNC, NONE, READ),
    ("task cancel", "cancel_task", SYNC, NONE, DESTRUCTIVE),
    ("task list", "list_tasks", SYNC, NONE, READ),
    ("task watch ls", "list_watches", SYNC, NONE, READ),
    ("task watch show", "get_watch", SYNC, NONE, READ),
    ("task watch rm", "delete_watch", SYNC, NONE, DESTRUCTIVE),
    ("task watch pause", None, SYNC, NONE, MUTATING),
    ("task watch resume", None, SYNC, NONE, MUTATING),
    ("task watch update", "update_watch", SYNC, NONE, MUTATING),
    ("task watch trigger", "trigger_watch", SYNC, NONE, MUTATING),
    ("session new", "create_session", SYNC, NONE, MUTATING),
    ("session list", "list_sessions", SYNC, NONE, READ),
    ("session get", "get_session", SYNC, NONE, READ),
    ("session get-session-context", "get_session_context", SYNC, NONE, READ),
    ("session get-session-archive", "get_session_archive", SYNC, NONE, READ),
    ("session delete", "delete_session", SYNC, NONE, DESTRUCTIVE),
    ("session add-message", "add_message", SYNC, NONE, MUTATING),
    ("session add-messages", "batch_add_messages", SYNC, NONE, MUTATING),
    ("session config set", "update_session_config", SYNC, NONE, MUTATING),
    ("session commit", "commit_session", NATIVE, NONE, MUTATING),
    ("snapshot commit", "git_commit", SYNC, NONE, MUTATING),
    ("snapshot restore", "git_restore", SYNC, NONE, DESTRUCTIVE),
    ("snapshot show", "git_show", SYNC, NONE, READ),
    ("snapshot log", "git_log", SYNC, NONE, READ),
    ("snapshot diff", "git_diff", SYNC, NONE, READ),
    ("snapshot ignore-get", "git_get_ignore", SYNC, NONE, READ),
    ("snapshot ignore-set", "git_set_ignore", SYNC, NONE, MUTATING),
    ("snapshot ignore-delete", "git_delete_ignore", SYNC, NONE, DESTRUCTIVE),
    ("privacy categories", None, SYNC, NONE, READ),
    ("privacy list", None, SYNC, NONE, READ),
    ("privacy get", None, SYNC, NONE, READ),
    ("privacy upsert", None, SYNC, NONE, MUTATING),
    ("privacy versions", None, SYNC, NONE, READ),
    ("privacy version", None, SYNC, NONE, READ),
    ("privacy activate", None, SYNC, NONE, MUTATING),
    ("admin create-account", "admin_create_account", SYNC, NONE, PRIVILEGED),
    ("admin list-accounts", "admin_list_accounts", SYNC, NONE, PRIVILEGED),
    ("admin delete-account", "admin_delete_account", SYNC, NONE, PRIVILEGED),
    ("admin migrate", "admin_migrate", NATIVE, NONE, PRIVILEGED),
    ("admin register-user", "admin_register_user", SYNC, NONE, PRIVILEGED),
    ("admin list-users", "admin_list_users", SYNC, NONE, PRIVILEGED),
    ("admin create-group", "admin_create_group", SYNC, NONE, PRIVILEGED),
    ("admin list-groups", "admin_list_groups", SYNC, NONE, PRIVILEGED),
    ("admin list-group-members", "admin_list_group_members", SYNC, NONE, PRIVILEGED),
    ("admin add-group-member", "admin_add_group_member", SYNC, NONE, PRIVILEGED),
    ("admin remove-group-member", "admin_remove_group_member", SYNC, NONE, PRIVILEGED),
    ("admin delete-group", "admin_delete_group", SYNC, NONE, PRIVILEGED),
    ("admin remove-user", "admin_remove_user", SYNC, NONE, PRIVILEGED),
    ("admin set-role", "admin_set_role", SYNC, NONE, PRIVILEGED),
    ("admin regenerate-key", "admin_regenerate_key", SYNC, NONE, PRIVILEGED),
    ("admin set-account-settings", None, SYNC, NONE, PRIVILEGED),
    ("observer queue", None, SYNC, NONE, READ),
    ("observer vikingdb", None, SYNC, NONE, READ),
    ("observer models", None, SYNC, NONE, READ),
    ("observer retrieval", None, SYNC, NONE, READ),
    ("observer filesystem", None, SYNC, NONE, READ),
    ("observer system", None, SYNC, NONE, READ),
    ("system wait", "wait_processed", WAIT, NONE, READ),
    ("system status", "get_status", SYNC, NONE, READ),
    ("system health", "health", SYNC, NONE, READ),
    ("system consistency", "check_consistency", SYNC, NONE, READ),
    ("system backend sync-status", None, SYNC, NONE, READ),
    ("system backend sync-retry", None, SYNC, NONE, MUTATING),
]

LOCAL_ONLY_COMMANDS = {"config", "language", "version", "tui"}
LOCAL_ONLY_LEAF_COMMANDS = {"system crypto init-key"}

REST_OPERATIONS = {
    "add-memory": "create_session_api_v1_sessions_post",
    "cp": "cp_api_v1_fs_cp_post",
    "attrs get": "attrs_api_v1_fs_attrs_get",
    "abstract": "abstract_api_v1_content_abstract_get",
    "chat": "chat_bot_v1_chat_post",
    "compile": "create_compile_api_v1_compile_post",
    "task watch pause": "patch_watch_by_id_api_v1_watches__task_id__patch",
    "task watch resume": "patch_watch_by_id_api_v1_watches__task_id__patch",
    "privacy categories": "list_privacy_categories_api_v1_privacy_configs_get",
    "privacy list": "list_privacy_targets_api_v1_privacy_configs__category__get",
    "privacy get": "get_privacy_current_api_v1_privacy_configs__category___target_key__get",
    "privacy upsert": "upsert_privacy_config_api_v1_privacy_configs__category___target_key__post",
    "privacy versions": "list_privacy_versions_api_v1_privacy_configs__category___target_key__versions_get",
    "privacy version": "get_privacy_version_api_v1_privacy_configs__category___target_key__versions__version__get",
    "privacy activate": "activate_privacy_version_api_v1_privacy_configs__category___target_key__activate_post",
    "admin set-account-settings": "patch_account_settings_api_v1_admin_accounts__account_id__settings_patch",
    "observer queue": "observer_queue_api_v1_observer_queue_get",
    "observer vikingdb": "observer_vikingdb_api_v1_observer_vikingdb_get",
    "observer models": "observer_models_api_v1_observer_models_get",
    "observer retrieval": "observer_retrieval_api_v1_observer_retrieval_get",
    "observer filesystem": "observer_filesystem_api_v1_observer_filesystem_get",
    "observer system": "observer_system_api_v1_observer_system_get",
    "system backend sync-status": "backend_sync_status_api_v1_system_backend_sync_status_post",
    "system backend sync-retry": "backend_sync_retry_api_v1_system_backend_sync_retry_post",
}


def cli_binary() -> str:
    packaged = ROOT / ".venv" / "Scripts" / "ov.exe"
    if packaged.is_file():
        return str(packaged)
    raise RuntimeError("OpenViking CLI binary was not found in the repository virtual environment")


_HELP_CACHE: dict[tuple[str, ...], str] = {}


def cli_help(words: list[str]) -> str:
    key = tuple(words)
    if key not in _HELP_CACHE:
        completed = subprocess.run(
            [cli_binary(), *words, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        _HELP_CACHE[key] = completed.stdout
    return _HELP_CACHE[key]


def cli_description(words: list[str]) -> str:
    lines: list[str] = []
    for line in cli_help(words).splitlines():
        stripped = line.strip()
        if stripped.startswith("Usage:"):
            break
        if stripped:
            lines.append(stripped)
    if lines and lines[0].startswith("OpenViking v"):
        lines = lines[1:]
    if not lines:
        if len(words) > 1:
            return f"{cli_description(words[:-1])}: {words[-1]}"
        raise RuntimeError(f"Could not derive description for {' '.join(words)}")
    return lines[0]


def _cli_version() -> str:
    match = re.search(r"OpenViking v(\S+)", cli_help([]))
    if match is None:
        raise RuntimeError("Could not derive OpenViking CLI version")
    return match.group(1)


def _root_cli_commands() -> set[str]:
    commands: set[str] = set()
    for line in cli_help([]).splitlines():
        stripped = line.strip().lstrip("│").strip()
        match = re.match(r"^(?:ov )?([a-z][a-z0-9_-]*)(?:\s+\S|\[)", stripped)
        if match and match.group(1) != "ov":
            commands.add(match.group(1))
    return commands - LOCAL_ONLY_COMMANDS


def _subcommands(words: list[str]) -> list[str]:
    output = cli_help(words)
    in_section = False
    commands: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in {"Subcommands", "Commands:"}:
            in_section = True
            continue
        if not in_section:
            continue
        if not stripped or stripped in {"Global options", "Options", "Next"}:
            break
        match = re.match(r"^([a-z][a-z0-9_-]*)(?:\s|$)", stripped)
        if match is not None:
            commands.append(match.group(1))
    return [name for name in commands if name != "help"]


def discover_cli_commands() -> set[str]:
    def visit(words: list[str]) -> list[list[str]]:
        subcommands = _subcommands(words)
        if not subcommands:
            return [words]
        leaves: list[list[str]] = []
        parent_help = cli_help(words)
        for subcommand in subcommands:
            child = [*words, subcommand]
            # Some OpenViking group implementations return parent help for an
            # unknown nested invocation. Treat that advertised child as a leaf.
            if cli_help(child) == parent_help:
                leaves.append(child)
                continue
            leaves.extend(visit(child))
        return leaves

    leaves: set[str] = set()
    for command in sorted(_root_cli_commands()):
        for leaf in visit([command]):
            leaves.add(" ".join(leaf))
    return leaves


def validate_cli_scope(command_names: list[str]) -> None:
    expected = discover_cli_commands() - LOCAL_ONLY_LEAF_COMMANDS
    supplied = set(command_names)
    missing = sorted(expected - supplied)
    extra = sorted(supplied - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing: {missing}")
        if extra:
            details.append(f"extra: {extra}")
        raise ValueError("CLI semantic command scope drift: " + "; ".join(details))


def annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        parts = [arg for arg in args if arg is not type(None)]
        allows_null = type(None) in args
        if len(parts) == 1:
            nested = annotation_schema(parts[0])
            if len(nested) == 1 and "type" in nested and isinstance(nested["type"], str):
                return {**nested, "type": [nested["type"], "null"]}
            if not nested:
                return {}
            return {"anyOf": [nested, {"type": "null"}]} if allows_null else nested
        options = [annotation_schema(part) for part in parts]
        if allows_null:
            options.append({"type": "null"})
        return {"anyOf": options}

    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bytes:
        return {"type": "string", "contentEncoding": "base64"}
    if annotation is type(None):
        return {"type": "null"}
    if origin in (list, typing.List):
        item = annotation_schema(args[0]) if args else {}
        return {"type": "array", "items": item}
    if origin in (dict, typing.Dict):
        return {"type": "object"}
    if origin is tuple:
        return {"type": "array"}
    if isinstance(annotation, type) and issubclass(annotation, str):
        return {"type": "string"}
    if isinstance(annotation, type) and issubclass(annotation, int):
        return {"type": "integer"}
    return {"type": "object"}


def method_schema(method: Any) -> dict[str, Any]:
    signature = inspect.signature(method)
    hints = typing.get_type_hints(method)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self" or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        schema = annotation_schema(hints.get(name, parameter.annotation))
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif _json_default(parameter.default) is not inspect.Parameter.empty:
            schema["default"] = _json_default(parameter.default)
        properties[name] = schema

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _json_default(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return inspect.Parameter.empty


def installed_openapi() -> dict[str, Any]:
    from openviking.server.app import create_app
    from openviking.server.config import ServerConfig

    return create_app(ServerConfig(), service=object()).openapi()


def _openapi_operations(document: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    operations: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            if isinstance(operation_id, str):
                operations[operation_id] = (method.upper(), path, operation)
    return operations


def _clean_schema(value: Any, document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    if "$ref" in value:
        value = _resolve_openapi_schema(value, document)
    output: dict[str, Any] = {}
    if "type" in value:
        output["type"] = value["type"]
    if "anyOf" in value:
        output["anyOf"] = [_clean_schema(option, document) for option in value["anyOf"]]
    if "items" in value:
        output["items"] = _clean_schema(value["items"], document)
    if "properties" in value and isinstance(value["properties"], Mapping):
        output["properties"] = {
            name: _clean_schema(schema, document)
            for name, schema in value["properties"].items()
        }
    if "required" in value and isinstance(value["required"], list):
        output["required"] = value["required"]
    if "additionalProperties" in value:
        output["additionalProperties"] = value["additionalProperties"]
    for name in ("default", "enum", "minItems", "minLength", "contentEncoding"):
        if name in value:
            output[name] = value[name]
    return output


def _resolve_openapi_schema(
    schema: Mapping[str, Any], document: dict[str, Any]
) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/components/schemas/"):
        return schema
    value: Any = document
    for part in reference[len("#/") :].split("/"):
        if not isinstance(value, Mapping) or part not in value:
            return schema
        value = value[part]
    return value if isinstance(value, Mapping) else schema


def _custom_input_schema(name: str) -> dict[str, Any] | None:
    if name == "add-memory":
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"content": {}},
            "required": ["content"],
            "additionalProperties": False,
        }
    if name == "attrs get":
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"uri": {"type": "string"}, "key": {"type": "string"}},
            "required": ["uri"],
            "additionalProperties": False,
        }
    if name == "chat":
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "session_id": {"type": "string"},
                "sender_id": {"type": "string"},
                "stream": {"type": "boolean", "default": False},
            },
            "required": ["message"],
            "additionalProperties": False,
        }
    if name in {"task watch pause", "task watch resume"}:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "to_uri": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        }
    return None


def _sdk_operation_ids(
    method_name: str, operations: dict[str, tuple[str, str, dict[str, Any]]]
) -> list[str]:
    routes = _sdk_routes(method_name)
    if not routes:
        raise ValueError(f"Installed SDK method has no HTTP route: {method_name}")
    normalized_server_routes = {
        (method, _openapi_path_pattern(path)): operation_id
        for operation_id, (method, path, _) in operations.items()
    }
    matched = []
    for method, route in routes:
        operation_id = normalized_server_routes.get((method, route))
        if operation_id is None:
            raise ValueError(
                f"Installed SDK route is absent from OpenViking OpenAPI: "
                f"{method_name} {method} {route}"
            )
        if operation_id not in matched:
            matched.append(operation_id)
    return matched


def _sdk_routes(method_name: str) -> list[tuple[str, str]]:
    method = getattr(AsyncHTTPClient, method_name, None)
    if not inspect.isfunction(method):
        return []
    return list(_visit_sdk_function(method_name, set()))


def _visit_sdk_function(
    method_name: str, visited: set[str]
) -> list[tuple[str, str]]:
    if method_name in visited:
        return []
    visited.add(method_name)
    method = getattr(AsyncHTTPClient, method_name)
    tree = ast.parse(dedent(inspect.getsource(method)))
    routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attribute = node.func.attr
        arguments = node.args
        route: str | None = None
        http_method: str | None = None
        if attribute in {"get", "post", "put", "patch", "delete"} and arguments:
            http_method = attribute.upper()
            route = _route_pattern(arguments[0])
        elif attribute in {"request", "_request"} and len(arguments) >= 2:
            http_method = _route_pattern(arguments[0])
            route = _route_pattern(arguments[1])
        if (
            http_method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            and route is not None
            and route.startswith("/")
        ):
            entry = (http_method, route)
            if entry not in routes:
                routes.append(entry)
            continue
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and attribute not in {"request", "_request"}
            and inspect.isfunction(getattr(AsyncHTTPClient, attribute, None))
        ):
            for nested_route in _visit_sdk_function(attribute, visited):
                if nested_route not in routes:
                    routes.append(nested_route)
    return routes


def _route_pattern(expression: ast.AST) -> str | None:
    if isinstance(expression, ast.Constant):
        return str(expression.value) if isinstance(expression.value, str) else None
    if isinstance(expression, ast.JoinedStr):
        parts: list[str] = []
        for value in expression.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            else:
                parts.append("*")
        return "".join(parts)
    return None


def _openapi_path_pattern(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "*", path)


def _routes_for_operations(
    operation_ids: list[str],
    operations: dict[str, tuple[str, str, dict[str, Any]]],
) -> dict[str, tuple[str, str]]:
    return {
        operation_id: (operations[operation_id][0], operations[operation_id][1])
        for operation_id in operation_ids
    }


def _fallback_operations(name: str, operation_id: str) -> list[str]:
    if name == "add-memory":
        return [
            "create_session_api_v1_sessions_post",
            "batch_add_messages_api_v1_sessions__session_id__messages_batch_post",
            "commit_session_api_v1_sessions__session_id__commit_post",
        ]
    if name in {"task watch pause", "task watch resume"}:
        return [
            operation_id,
            "patch_watch_by_uri_api_v1_watches_patch",
        ]
    if name == "chat":
        return [operation_id, "chat_stream_bot_v1_chat_stream_post"]
    return [operation_id]


def _rest_definition(
    name: str, document: dict[str, Any]
) -> tuple[str, str, str, dict[str, Any], dict[str, tuple[str, dict[str, Any]]]]:
    operations = _openapi_operations(document)
    operation_id = REST_OPERATIONS[name]
    if operation_id not in operations:
        raise ValueError(f"OpenViking OpenAPI operation is missing: {operation_id}")
    method, path, operation = operations[operation_id]

    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters: dict[str, tuple[str, dict[str, Any]]] = {}

    for parameter in operation.get("parameters", []):
        if parameter.get("in") == "header":
            continue
        parameter_name = parameter["name"]
        schema = _clean_schema(parameter.get("schema", {}), document)
        properties[parameter_name] = schema
        parameters[parameter_name] = (parameter["in"], schema)
        if parameter.get("required"):
            required.append(parameter_name)

    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {}) if isinstance(request_body, Mapping) else {}
    body_schema = content.get("application/json", {}).get("schema", {})
    body_schema = _clean_schema(body_schema, document)
    for property_name, schema in body_schema.get("properties", {}).items():
        properties[property_name] = schema
        parameters[property_name] = ("body", schema)
    required.extend(
        name for name in body_schema.get("required", []) if name not in required
    )

    if name in {"task watch pause", "task watch resume"}:
        by_uri = operations["patch_watch_by_uri_api_v1_watches_patch"]
        for parameter in by_uri[2].get("parameters", []):
            if parameter.get("in") == "query":
                schema = _clean_schema(parameter.get("schema", {}), document)
                properties[parameter["name"]] = schema
                parameters[parameter["name"]] = ("query", schema)

    input_schema = _custom_input_schema(name)
    if input_schema is None:
        input_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    else:
        parameters = {
            name: (location, schema)
            for name, (location, schema) in parameters.items()
            if name in input_schema["properties"] or location == "filter"
        }
        if name == "attrs get":
            parameters["key"] = ("filter", {"type": "string"})
        if name == "add-memory":
            parameters["content"] = ("body", {})
        if name == "chat":
            for property_name in input_schema["properties"]:
                parameters[property_name] = ("body", input_schema["properties"][property_name])

    return operation_id, method, path, input_schema, parameters


def build_manifest() -> dict[str, Any]:
    validate_cli_scope([name for name, *_ in COMMANDS])
    openapi = installed_openapi()
    commands: list[dict[str, Any]] = []
    for name, sdk_method, asynchronous, file_input, danger in COMMANDS:
        if name.split()[0] in LOCAL_ONLY_COMMANDS or name in LOCAL_ONLY_LEAF_COMMANDS:
            raise ValueError(f"Local-only command entered the manifest: {name}")
        operations = _openapi_operations(openapi)
        if sdk_method:
            transport = "sdk"
            http_operation_ids = _sdk_operation_ids(sdk_method, operations)
            http_operation = http_operation_ids[0]
            http_method = None
            http_path = None
            http_parameters = None
            input_schema = method_schema(getattr(AsyncHTTPClient, sdk_method))
        else:
            transport = "rest_fallback"
            http_operation, http_method, http_path, input_schema, http_parameters = (
                _rest_definition(name, openapi)
            )
            http_operation_ids = _fallback_operations(name, http_operation)
        http_routes = _routes_for_operations(http_operation_ids, operations)
        http_operation_schemas = {
            operation_id: openapi_operation_schema(
                operations[operation_id][2], openapi
            )
            for operation_id in http_operation_ids
        }
        http_bridge = "sdk" if sdk_method else "direct"
        if not sdk_method:
            if name == "add-memory":
                http_bridge = "add_memory"
            elif name == "attrs get":
                http_bridge = "attrs_filter"
            elif name == "chat":
                http_bridge = "chat"
            elif name in {"task watch pause", "task watch resume"}:
                http_bridge = "watch_toggle"
        command = {
            "name": name,
            "description": cli_description(name.split()),
            "transport": transport,
            "sdk_method": sdk_method,
            "http_operation": http_operation,
            "input_schema": input_schema,
            "asynchronous": asynchronous,
            "file_input": file_input,
            "danger": danger,
            "http_method": http_method,
            "http_path": http_path,
            "http_parameters": (
                None
                if http_parameters is None
                else {
                    name: [location, schema]
                    for name, (location, schema) in http_parameters.items()
                }
            ),
            "http_bridge": http_bridge,
            "http_operations": http_operation_ids,
            "http_routes": {
                operation_id: [method, path]
                for operation_id, (method, path) in http_routes.items()
            },
            "http_operation_schemas": http_operation_schemas,
            "http_schema_source": (
                "sdk_signature"
                if sdk_method
                else (
                    "cli_semantic"
                    if name
                    in {
                        "add-memory",
                        "attrs get",
                        "chat",
                        "task watch pause",
                        "task watch resume",
                    }
                    else "openapi"
                )
            ),
        }
        commands.append(command)

    return {
        "schema_version": 1,
        "source": "openviking-cli",
        "source_version": _cli_version(),
        "sdk_version": _package_version("openviking-sdk"),
        "commands": commands,
    }


def _package_version(name: str) -> str:
    from importlib.metadata import version

    return version(name)


def main() -> int:
    manifest = build_manifest()
    OUTPUT_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH} with {len(manifest['commands'])} commands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
