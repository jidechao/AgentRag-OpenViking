"""OpenViking Server adapters."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any
from urllib.parse import quote

import httpx
from openviking_sdk.client import AsyncHTTPClient
from openviking_sdk.errors import OpenVikingError

from .config import Settings
from .health import DependencyHealth, DependencyStatus


class HttpOpenVikingHealthAdapter:
    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ):
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=str(settings.openviking_base_url),
            timeout=2.0,
        )

    async def check(self) -> DependencyHealth:
        try:
            response = await self._client.get("/health")
        except httpx.HTTPError:
            return DependencyHealth(
                status=DependencyStatus.DOWN,
                reachable=False,
                detail="OpenViking Server is unreachable",
            )

        if response.is_success:
            return DependencyHealth(status=DependencyStatus.UP, reachable=True)

        return DependencyHealth(
            status=DependencyStatus.DOWN,
            reachable=False,
            detail=f"OpenViking Server returned HTTP {response.status_code}",
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# OpenViking SDK 0.1.10 exposes get_status as a blocking run_async wrapper.
# Use its installed async coroutine directly so command calls stay on the service loop.
_ASYNC_SDK_METHODS = {"get_status": "_get_system_status"}


class SdkOpenVikingCommandClient:
    """Thin SDK-first adapter; no OpenViking CLI subprocess is involved."""

    def __init__(
        self,
        *,
        settings: Settings,
        client: AsyncHTTPClient | None = None,
        rest_client: httpx.AsyncClient | None = None,
    ):
        self._owns_client = client is None
        self._owns_rest_client = rest_client is None
        self._settings = settings
        self._rest_client = rest_client or httpx.AsyncClient(
            base_url=str(settings.openviking_base_url),
            timeout=30.0,
        )
        self._sdk_initialized = False
        self._client = client or AsyncHTTPClient(
            url=str(settings.openviking_base_url),
            api_key=(
                settings.openviking_api_key.get_secret_value()
                if Settings._has_secret(settings.openviking_api_key)
                else None
            ),
        )

    async def initialize(self) -> None:
        if self._sdk_initialized:
            return
        initialize = getattr(self._client, "initialize", None)
        if initialize is not None:
            await initialize()
        self._sdk_initialized = True

    async def call(self, method_name: str, arguments: Mapping[str, Any]) -> Any:
        await self.initialize()
        selected_method_name = _ASYNC_SDK_METHODS.get(method_name, method_name)
        method = getattr(self._client, selected_method_name)
        result = method(**arguments)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def call_rest(
        self,
        method: str,
        path: str,
        *,
        path_parameters: Mapping[str, Any],
        query_parameters: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> Any:
        rendered_path = path
        for name, value in path_parameters.items():
            rendered_path = rendered_path.replace(
                f"{{{name}}}", quote(str(value), safe="")
            )
        try:
            response = await self._rest_client.request(
                method,
                rendered_path,
                params=dict(query_parameters) or None,
                json=dict(body) or None,
                headers=self._rest_headers(),
            )
        except httpx.HTTPError as error:
            raise OpenVikingError(
                "OpenViking Server is unreachable",
                code="UNAVAILABLE",
                details={"error_type": type(error).__name__},
            ) from None

        return self._rest_result(response)

    async def get_openapi(self) -> dict[str, Any]:
        try:
            response = await self._rest_client.get(
                "/openapi.json", headers=self._rest_headers()
            )
        except httpx.HTTPError as error:
            raise OpenVikingError(
                "OpenViking OpenAPI document is unreachable",
                code="UNAVAILABLE",
                details={"error_type": type(error).__name__},
            ) from None
        if not response.is_success:
            raise OpenVikingError(
                "OpenViking OpenAPI document is unavailable",
                code="UNAVAILABLE",
                details={"status_code": response.status_code},
            )
        try:
            document = response.json()
        except ValueError as error:
            raise OpenVikingError(
                "OpenViking OpenAPI document is not JSON",
                code="INTERNAL",
            ) from None
        if not isinstance(document, dict):
            raise OpenVikingError(
                "OpenViking OpenAPI document has an invalid shape",
                code="INTERNAL",
            )
        return document

    def _rest_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if Settings._has_secret(self._settings.openviking_api_key):
            headers["X-API-Key"] = self._settings.openviking_api_key.get_secret_value()
        return headers

    @staticmethod
    def _rest_result(response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        try:
            data = response.json()
        except ValueError:
            if response.is_success:
                if "text/event-stream" in content_type:
                    return {"text": response.text}
                return response.text
            raise OpenVikingError(
                "OpenViking HTTP request failed",
                code=_http_error_code(response.status_code),
                details={"status_code": response.status_code},
            ) from None

        if isinstance(data, Mapping) and data.get("status") == "error":
            error = data.get("error", {})
            if isinstance(error, Mapping):
                raise OpenVikingError(
                    str(error.get("message", "OpenViking HTTP request failed")),
                    code=str(error.get("code", "UNKNOWN")),
                    details=dict(error.get("details") or {}),
                )
        if not response.is_success:
            detail = data.get("detail") if isinstance(data, Mapping) else None
            raise OpenVikingError(
                detail if isinstance(detail, str) else "OpenViking HTTP request failed",
                code=_http_error_code(response.status_code),
                details={"status_code": response.status_code},
            )
        if isinstance(data, Mapping) and "result" in data:
            return data["result"]
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()
            self._sdk_initialized = False
        if self._owns_rest_client:
            await self._rest_client.aclose()


def _http_error_code(status_code: int) -> str:
    if status_code == 401:
        return "UNAUTHENTICATED"
    if status_code == 403:
        return "PERMISSION_DENIED"
    if status_code == 404:
        return "NOT_FOUND"
    if status_code == 409:
        return "CONFLICT"
    if status_code == 429:
        return "RESOURCE_EXHAUSTED"
    if status_code >= 500:
        return "INTERNAL"
    return "INVALID_ARGUMENT"
