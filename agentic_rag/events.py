"""Stable Agent Event Stream contract and citation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_VIKING_URI = re.compile(
    r"(?<![A-Za-z0-9_])viking://[^\s<>'\"`]+",
    re.IGNORECASE,
)


def event_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_viking_uri(value: str) -> str | None:
    candidate = value.strip()
    for marker in ("\\n", "\\r", "\\t", "](", "<"):
        candidate = candidate.split(marker, 1)[0]
    candidate = candidate.rstrip(".,;:!?)]}。，；：！？）》】」』”’")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if not parsed.netloc or not parsed.netloc.isascii():
        return None
    return urlunsplit(
        ("viking", parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def extract_viking_citations(value: Any) -> list[str]:
    citations: list[str] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            for match in _VIKING_URI.finditer(item):
                citation = _normalize_viking_uri(match.group(0))
                if citation is not None and citation not in seen:
                    seen.add(citation)
                    citations.append(citation)
        elif isinstance(item, Mapping):
            for key, nested in item.items():
                visit(str(key))
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return citations
