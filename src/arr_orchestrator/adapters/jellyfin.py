from __future__ import annotations

import math
import posixpath
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from ..transport import TransportFailure

_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2,3}(?:[-+][0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$")
_MAX_ITEMS = 10_000
_ALLOWED_TASK_STATES = {"idle", "running", "cancelling"}
_ALLOWED_COLLECTION_TYPES = {
    "books", "boxsets", "homevideos", "mixed", "movies", "music",
    "musicvideos", "tvshows",
}


class JellyfinAdapterFailure(RuntimeError):
    pass


class ReadOnlyTransport(Protocol):
    def get_json(self, path: str) -> dict[str, Any] | list[Any]: ...
    def get_json_fields(self, path: str, fields: tuple[str, ...]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class JellyfinCapabilities:
    server_version: str
    resources: tuple[str, ...] = ("health", "libraries", "refresh_status")


@dataclass(frozen=True, slots=True)
class JellyfinHealth:
    healthy: bool
    startup_complete: bool
    detailed_status_supported: bool = False


@dataclass(frozen=True, slots=True)
class JellyfinLibrary:
    collection_type: str
    locations: tuple[str, ...]
    refreshing: bool


@dataclass(frozen=True, slots=True)
class JellyfinRefreshStatus:
    supported: bool
    state: str
    progress_percent: float | None


@dataclass(frozen=True, slots=True)
class JellyfinSnapshot:
    capabilities: JellyfinCapabilities
    health: JellyfinHealth
    libraries: tuple[JellyfinLibrary, ...]
    refresh: JellyfinRefreshStatus


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    if len(value) > _MAX_ITEMS:
        raise JellyfinAdapterFailure("RESPONSE_BOUNDS_INVALID")
    return value


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _string(value: Any, *, lower: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    if any(ord(char) == 0x7F or unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value.lower() if lower else value


def _location(value: Any) -> str:
    path = _string(value)
    parsed = PurePosixPath(path)
    if (
        not path.startswith("/")
        or path == "/"
        or "%" in path
        or "\\" in path
        or ".." in parsed.parts
        or "." in parsed.parts
        or "//" in path
        or posixpath.normpath(path) != path
    ):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    return path


def _progress(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
    return result


class JellyfinAdapter:
    def __init__(self, transport: ReadOnlyTransport):
        self.transport = transport

    def _json(self, path: str) -> dict[str, Any] | list[Any]:
        failure: str | None = None
        try:
            return self.transport.get_json(path)
        except TransportFailure as error:
            mapping = {
                "AUTH_FAILED": "AUTH_FAILED",
                "CREDENTIAL_INVALID": "CREDENTIAL_INVALID",
                "RESOURCE_NOT_FOUND": "RESOURCE_NOT_FOUND",
                "TLS_VERIFICATION_FAILED": "SERVICE_UNREACHABLE",
                "DEADLINE_EXCEEDED": "SERVICE_UNREACHABLE",
                "SERVICE_UNREACHABLE": "SERVICE_UNREACHABLE",
            }
            failure = mapping.get(error.code, "RESPONSE_INVALID")
        raise JellyfinAdapterFailure(failure)

    def _public_system_info(self) -> dict[str, Any]:
        failure: str | None = None
        try:
            return self.transport.get_json_fields(
                "/System/Info/Public",
                ("Version", "StartupWizardCompleted"),
            )
        except TransportFailure as error:
            mapping = {
                "AUTH_FAILED": "AUTH_FAILED",
                "CREDENTIAL_INVALID": "CREDENTIAL_INVALID",
                "RESOURCE_NOT_FOUND": "RESOURCE_NOT_FOUND",
                "TLS_VERIFICATION_FAILED": "SERVICE_UNREACHABLE",
                "DEADLINE_EXCEEDED": "SERVICE_UNREACHABLE",
                "SERVICE_UNREACHABLE": "SERVICE_UNREACHABLE",
            }
            failure = mapping.get(error.code, "RESPONSE_INVALID")
        raise JellyfinAdapterFailure(failure)

    def read_snapshot(self) -> JellyfinSnapshot:
        system = _mapping(self._public_system_info())
        version = _string(system.get("Version"))
        if _VERSION.fullmatch(version) is None:
            raise JellyfinAdapterFailure("UNSUPPORTED_API_VERSION")
        health = JellyfinHealth(
            healthy=_bool(system.get("StartupWizardCompleted")),
            startup_complete=_bool(system.get("StartupWizardCompleted")),
        )

        libraries: list[JellyfinLibrary] = []
        all_locations: set[str] = set()
        for raw in _list(self._json("/Library/VirtualFolders")):
            item = _mapping(raw)
            collection_type = _string(item.get("CollectionType"))
            if collection_type not in _ALLOWED_COLLECTION_TYPES:
                raise JellyfinAdapterFailure("UNSUPPORTED_CAPABILITY")
            raw_locations = _list(item.get("Locations"))
            if not raw_locations:
                raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
            locations = tuple(_location(value) for value in raw_locations)
            if len(set(locations)) != len(locations) or any(path in all_locations for path in locations):
                raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
            all_locations.update(locations)
            libraries.append(JellyfinLibrary(collection_type, locations, _progress(item.get("RefreshProgress")) is not None))

        selected: Mapping[str, Any] | None = None
        for raw in _list(self._json("/ScheduledTasks")):
            task = _mapping(raw)
            key = task.get("Key")
            if key == "RefreshLibrary":
                if selected is not None:
                    raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
                selected = task
        if selected is None:
            refresh = JellyfinRefreshStatus(False, "unsupported", None)
        else:
            state = _string(selected.get("State"), lower=True)
            if state not in _ALLOWED_TASK_STATES:
                raise JellyfinAdapterFailure("RESPONSE_SHAPE_INVALID")
            refresh = JellyfinRefreshStatus(True, state, _progress(selected.get("CurrentProgressPercentage")))

        return JellyfinSnapshot(
            JellyfinCapabilities(version),
            health,
            tuple(libraries),
            refresh,
        )
