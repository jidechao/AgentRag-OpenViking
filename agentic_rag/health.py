"""Health values shared by the application seam and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"


class DependencyStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True)
class DependencyHealth:
    status: DependencyStatus
    reachable: bool
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reachable": self.reachable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ApplicationHealth:
    status: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status}


@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    application: ApplicationHealth
    dependencies: dict[str, DependencyHealth]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "application": self.application.as_dict(),
            "dependencies": {
                name: dependency.as_dict()
                for name, dependency in self.dependencies.items()
            },
        }
