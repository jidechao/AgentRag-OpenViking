"""Generated OpenViking command catalog, validation, and response envelopes."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("command_manifest.json")


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    description: str
    transport: str
    sdk_method: str | None
    http_operation: str | None
    input_schema: dict[str, Any]
    asynchronous: str
    file_input: str
    danger: str
    http_method: str | None = None
    http_path: str | None = None
    http_parameters: dict[str, tuple[str, dict[str, Any]]] | None = None
    http_schema_source: str | None = None
    http_operations: tuple[str, ...] = ()
    http_routes: dict[str, tuple[str, str]] | None = None
    http_bridge: str = "direct"
    http_operation_schemas: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.http_parameters is not None:
            object.__setattr__(
                self,
                "http_parameters",
                {
                    name: (location, dict(schema))
                    for name, (location, schema) in self.http_parameters.items()
                },
            )
        if self.http_routes is not None:
            object.__setattr__(
                self,
                "http_routes",
                {
                    operation_id: (method, path)
                    for operation_id, (method, path) in self.http_routes.items()
                },
            )
        if self.http_operation_schemas is not None:
            object.__setattr__(
                self,
                "http_operation_schemas",
                {key: dict(value) for key, value in self.http_operation_schemas.items()},
            )
        object.__setattr__(self, "http_operations", tuple(self.http_operations))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "transport": self.transport,
            "sdk_method": self.sdk_method,
            "http_operation": self.http_operation,
            "input_schema": self.input_schema,
            "asynchronous": self.asynchronous,
            "file_input": self.file_input,
            "danger": self.danger,
            "http_method": self.http_method,
            "http_path": self.http_path,
            "http_parameters": (
                None
                if self.http_parameters is None
                else {
                    name: [location, schema]
                    for name, (location, schema) in self.http_parameters.items()
                }
            ),
            "http_schema_source": self.http_schema_source,
            "http_bridge": self.http_bridge,
            "http_operations": list(self.http_operations),
            "http_operation_schemas": (
                None
                if self.http_operation_schemas is None
                else {
                    operation_id: dict(value)
                    for operation_id, value in self.http_operation_schemas.items()
                }
            ),
            "http_routes": (
                None
                if self.http_routes is None
                else {
                    operation_id: [method, path]
                    for operation_id, (method, path) in self.http_routes.items()
                }
            ),
        }


@dataclass(frozen=True)
class CommandError:
    code: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class CommandEnvelope:
    ok: bool
    data: Any
    command: str
    request_id: str
    error: CommandError | None

    @classmethod
    def success(cls, *, data: Any, command: str, request_id: str) -> "CommandEnvelope":
        return cls(True, json_safe(data), command, request_id, None)

    @classmethod
    def failure(
        cls,
        *,
        command: str,
        request_id: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> "CommandEnvelope":
        return cls(
            False,
            None,
            command,
            request_id,
            CommandError(code, message, details or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "command": self.command,
            "request_id": self.request_id,
            "error": self.error.as_dict() if self.error is not None else None,
        }


class CommandSurfaceDriftError(RuntimeError):
    """Material drift between the command manifest and OpenViking OpenAPI."""


class CommandValidationError(ValueError):
    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("Command arguments do not match the command schema")


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if value.__class__.__module__ == "enum":
        return value.value
    return value


class CommandCatalog:
    def __init__(self, commands: list[CommandDefinition], *, source: str = "openviking-cli"):
        self.source = source
        self._commands = tuple(commands)
        self._by_name = {command.name: command for command in self._commands}
        if len(self._by_name) != len(self._commands):
            raise ValueError("Command manifest contains duplicate command names")

    @classmethod
    def load(cls, path: Path | str = MANIFEST_PATH) -> "CommandCatalog":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        commands = [CommandDefinition(**item) for item in document["commands"]]
        return cls(commands, source=document["source"])

    @property
    def commands(self) -> tuple[CommandDefinition, ...]:
        return self._commands

    @property
    def names(self) -> set[str]:
        return set(self._by_name)

    def get(self, name: str) -> CommandDefinition:
        return self._by_name[name]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "commands": [command.as_dict() for command in self._commands],
        }

    def validate_against_openapi(self, document: Mapping[str, Any]) -> dict[str, Any]:
        paths = document.get("paths", {})
        if not isinstance(paths, Mapping):
            raise CommandSurfaceDriftError("OpenViking OpenAPI document has no paths object")

        operations: dict[str, tuple[str, str, Mapping[str, Any]]] = {}
        for path, path_item in paths.items():
            if not isinstance(path_item, Mapping):
                continue
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(operation, Mapping):
                    continue
                operation_id = operation.get("operationId")
                if isinstance(operation_id, str) and operation_id:
                    operations[operation_id] = (method.upper(), path, operation)

        warnings: list[str] = []
        failures: list[str] = []
        referenced_operations = [
            operation_id
            for command in self._commands
            for operation_id in (
                command.http_operations
                or ((command.http_operation,) if command.http_operation else ())
            )
        ]
        shared_operations = {
            operation_id
            for operation_id in referenced_operations
            if referenced_operations.count(operation_id) > 1
        }
        for command in self._commands:
            expected_operations = command.http_operations or (
                (command.http_operation,) if command.http_operation else ()
            )
            for operation_id in expected_operations:
                if operation_id not in operations:
                    failures.append(
                        f"{command.name}: HTTP operation is missing: {operation_id}"
                    )
                    continue
                actual_method, actual_path, _ = operations[operation_id]
                expected_route = (command.http_routes or {}).get(operation_id)
                if expected_route is None:
                    failures.append(
                        f"{command.name}: manifest route is missing: {operation_id}"
                    )
                elif expected_route != (actual_method, actual_path):
                    failures.append(
                        f"{command.name}: HTTP operation drift for {operation_id}: "
                        f"expected {expected_route[0]} {expected_route[1]}, got "
                        f"{actual_method} {actual_path}"
                    )
                expected_schema = (command.http_operation_schemas or {}).get(
                    operation_id
                )
                if expected_schema is None:
                    failures.append(
                        f"{command.name}: manifest schema is missing: {operation_id}"
                    )
                else:
                    actual_schema = openapi_operation_schema(
                        operations[operation_id][2], document
                    )
                    if expected_schema != actual_schema:
                        failures.append(
                            f"{command.name}: HTTP schema drift for {operation_id}"
                        )
            if command.transport != "rest_fallback":
                continue
            if command.http_operation not in operations:
                continue

            method, path, operation = operations[command.http_operation]
            if method != command.http_method or path != command.http_path:
                failures.append(
                    f"{command.name}: HTTP operation drift: expected "
                    f"{command.http_method} {command.http_path}, got {method} {path}"
                )

            description = operation.get("description")
            if (
                command.http_operation not in shared_operations
                and isinstance(description, str)
                and description != command.description
            ):
                warnings.append(
                    f"{command.name}: non-material description difference: "
                    f"{command.description!r} != {description!r}"
                )

            if command.http_schema_source != "cli_semantic":
                failures.extend(_validate_route_parameters(command, operation, document))
                failures.extend(_validate_body_schema(command, operation, document))

        if failures:
            raise CommandSurfaceDriftError(
                "OpenViking command surface drift: " + "; ".join(failures)
            )
        return {"warnings": warnings}

    def validate_arguments(
        self,
        command: CommandDefinition,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        supplied = {} if arguments is None else dict(arguments)
        schema = command.input_schema
        properties = schema.get("properties", {})
        errors: list[dict[str, str]] = []

        for name in schema.get("required", []):
            if name not in supplied:
                errors.append(
                    {
                        "path": name,
                        "code": "required",
                        "message": f"Required parameter is missing: {name}",
                    }
                )

        if schema.get("additionalProperties") is False:
            for name in supplied:
                if name not in properties:
                    errors.append(
                        {
                            "path": name,
                            "code": "unknown_parameter",
                            "message": f"Unknown parameter: {name}",
                        }
                    )

        for name, value in supplied.items():
            property_schema = properties.get(name)
            if property_schema is None or not _matches_schema(value, property_schema):
                expected = _describe_schema(property_schema)
                actual = _describe_value(value)
                errors.append(
                    {
                        "path": name,
                        "code": "type_error",
                        "message": f"Expected {expected}, got {actual}",
                    }
                )

        if errors:
            raise CommandValidationError(errors)
        return supplied


def openapi_operation_schema(
    operation: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, Any]:
    parameters = [
        {
            "name": parameter["name"],
            "in": parameter["in"],
            "required": bool(parameter.get("required", False)),
            "schema": _schema_contract(parameter.get("schema", {}), document),
        }
        for parameter in operation.get("parameters", [])
        if isinstance(parameter, Mapping) and parameter.get("in") != "header"
    ]
    request_body = operation.get("requestBody")
    body_schema = None
    body_required = False
    if isinstance(request_body, Mapping):
        body_required = bool(request_body.get("required", False))
        content = request_body.get("content", {})
        media = content.get("application/json", {}) if isinstance(content, Mapping) else {}
        if isinstance(media, Mapping) and media.get("schema") is not None:
            body_schema = _schema_contract(media["schema"], document)
    return {
        "parameters": parameters,
        "request_body": body_schema,
        "request_body_required": body_required,
    }


def _validate_route_parameters(
    command: CommandDefinition,
    operation: Mapping[str, Any],
    document: Mapping[str, Any],
) -> list[str]:
    server_parameters = {
        parameter.get("name"): parameter
        for parameter in operation.get("parameters", [])
        if isinstance(parameter, Mapping)
    }
    failures: list[str] = []
    required = set(command.input_schema.get("required", []))
    for name, (expected_location, expected_schema) in (
        command.http_parameters or {}
    ).items():
        if expected_location not in {"path", "query"}:
            continue
        parameter = server_parameters.get(name)
        if parameter is None:
            failures.append(f"{command.name}: parameter is missing: {name}")
            continue
        if parameter.get("in") != expected_location:
            failures.append(
                f"{command.name}: parameter location drift for {name}: expected "
                f"{expected_location}, got {parameter.get('in')}"
            )
        if bool(parameter.get("required", False)) != (name in required):
            failures.append(
                f"{command.name}: parameter requiredness drift for {name}"
            )
        actual_schema = _schema_contract(parameter.get("schema", {}), document)
        if actual_schema != _schema_contract(expected_schema, document):
            failures.append(f"{command.name}: parameter schema drift for {name}")

    for name, parameter in server_parameters.items():
        if parameter.get("in") in {"path", "query"} and name not in command.http_parameters:
            failures.append(
                f"{command.name}: unexpected server parameter: {name}"
            )
    return failures


def _validate_body_schema(
    command: CommandDefinition,
    operation: Mapping[str, Any],
    document: Mapping[str, Any],
) -> list[str]:
    expected_locations = command.http_parameters or {}
    has_body_parameters = any(
        location == "body" for location, _ in expected_locations.values()
    )
    request_body = operation.get("requestBody")
    if not isinstance(request_body, Mapping):
        if has_body_parameters:
            return [f"{command.name}: HTTP request body is missing"]
        return []
    content = request_body.get("content", {})
    if not isinstance(content, Mapping):
        if has_body_parameters:
            return [f"{command.name}: HTTP request body is missing"]
        return []
    media = content.get("application/json", {})
    if not isinstance(media, Mapping):
        if has_body_parameters:
            return [f"{command.name}: HTTP JSON request schema is missing"]
        return []
    schema = _resolve_schema(media.get("schema", {}), document)
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping) or not properties:
        if has_body_parameters:
            return [f"{command.name}: HTTP JSON request schema is empty"]
        return []

    expected = {
        name: schema
        for name, (location, schema) in (command.http_parameters or {}).items()
        if location == "body"
    }
    failures: list[str] = []
    if set(expected) != set(properties):
        failures.append(
            f"{command.name}: body property drift: expected {sorted(expected)}, "
            f"got {sorted(properties)}"
        )
    required = set(command.input_schema.get("required", [])) & set(expected)
    if required != set(schema.get("required", [])):
        failures.append(f"{command.name}: body requiredness drift")
    for name, expected_schema in expected.items():
        if name not in properties:
            continue
        actual_schema = _schema_contract(properties[name], document)
        if _schema_contract(expected_schema, document) != actual_schema:
            failures.append(f"{command.name}: body schema drift for {name}")
    return failures


def _resolve_schema(
    schema: Any, document: Mapping[str, Any]
) -> Mapping[str, Any]:
    if not isinstance(schema, Mapping) or "$ref" not in schema:
        return schema if isinstance(schema, Mapping) else {}
    reference = str(schema["$ref"])
    if not reference.startswith("#/components/schemas/"):
        return {}
    value: Any = document
    for part in reference[len("#/") :].split("/"):
        if not isinstance(value, Mapping) or part not in value:
            return {}
        value = value[part]
    return value if isinstance(value, Mapping) else {}


_SCHEMA_CONTRACT_KEYS = {
    "type",
    "anyOf",
    "items",
    "properties",
    "required",
    "additionalProperties",
    "default",
    "enum",
    "minItems",
    "minLength",
    "contentEncoding",
}


def _schema_contract(schema: Any, document: Mapping[str, Any]) -> dict[str, Any]:
    schema = _resolve_schema(schema, document) if isinstance(schema, Mapping) else {}
    contract = {
        key: value
        for key, value in schema.items()
        if key in _SCHEMA_CONTRACT_KEYS
    }
    if "anyOf" in contract:
        contract["anyOf"] = [
            _schema_contract(option, document) for option in contract["anyOf"]
        ]
    if "items" in contract:
        contract["items"] = _schema_contract(contract["items"], document)
    if "properties" in contract and isinstance(contract["properties"], Mapping):
        contract["properties"] = {
            name: _schema_contract(property_schema, document)
            for name, property_schema in contract["properties"].items()
        }
    return contract


def _schema_type(schema: Any, document: Mapping[str, Any] | None = None) -> set[str]:
    if not isinstance(schema, Mapping):
        return set()
    if document is not None:
        schema = _resolve_schema(schema, document)
    if "type" in schema:
        value = schema["type"]
        if isinstance(value, list):
            return set(value)
        return {str(value)}
    if "anyOf" in schema:
        types: set[str] = set()
        for option in schema["anyOf"]:
            types.update(_schema_type(option, document))
        return types
    return set()


def _matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    if not schema:
        return True
    if "anyOf" in schema:
        return any(_matches_schema(value, option) for option in schema["anyOf"])

    expected = schema.get("type")
    if expected is None:
        return True
    if isinstance(expected, list):
        if not any(_matches_type(value, item) for item in expected):
            return False
    elif not _matches_type(value, expected):
        return False

    if "enum" in schema and value not in schema["enum"]:
        return False

    if isinstance(value, str) and "minLength" in schema:
        if len(value) < schema["minLength"]:
            return False

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if "items" in schema:
            return all(_matches_schema(item, schema["items"]) for item in value)

    if isinstance(value, Mapping) and "properties" in schema:
        properties = schema["properties"]
        if schema.get("additionalProperties") is False:
            if any(name not in properties for name in value):
                return False
        if any(name not in value for name in schema.get("required", [])):
            return False
        return all(
            name in properties and _matches_schema(item, properties[name])
            for name, item in value.items()
        )

    return True


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "null":
        return value is None
    return True


def _describe_schema(schema: Mapping[str, Any] | None) -> str:
    if not schema:
        return "any JSON value"
    if "anyOf" in schema:
        return " or ".join(_describe_schema(option) for option in schema["anyOf"])
    return str(schema.get("type", "any JSON value"))


def _describe_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__
