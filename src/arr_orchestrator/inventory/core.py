from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol

REQUIRED_SERVICES = ("sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin")
_STATES = ("available", "partial", "unreachable", "unsupported", "unknown")
_UNREACHABLE = {"SERVICE_UNREACHABLE", "DEADLINE_EXCEEDED", "TLS_VERIFICATION_FAILED"}
_UNSUPPORTED = {"UNSUPPORTED_API_VERSION", "UNSUPPORTED_CAPABILITY"}
_SAFE_FAILURES = _UNREACHABLE | _UNSUPPORTED | {
    "AUTH_FAILED",
    "CONTENT_TYPE_INVALID",
    "CREDENTIAL_INVALID",
    "FIELD_PROJECTION_REQUIRED",
    "HTTP_STATUS_INVALID",
    "JSON_INVALID",
    "MUTATION_DISABLED",
    "ORIGIN_DENIED",
    "PATH_INVALID",
    "PRIVATE_DATA_REDACTED",
    "QUEUE_BOUNDS_INVALID",
    "QUEUE_PAGINATION_INVALID",
    "REDIRECT_DENIED",
    "RESOURCE_NOT_FOUND",
    "RESPONSE_BOUNDS_INVALID",
    "RESPONSE_INVALID",
    "RESPONSE_SHAPE_INVALID",
    "RESPONSE_TOO_LARGE",
    "SERVICE_IDENTITY_INVALID",
    "TEXT_INVALID",
}
_RESOURCES = {
    "sonarr": {"system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"},
    "radarr": {"system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"},
    "prowlarr": {"applications", "indexers", "system_status"},
    "qbittorrent": {"categories", "queue"},
    "jellyfin": {"health", "libraries", "refresh_status"},
}
_COLLECTION_TYPES = {"books", "boxsets", "homevideos", "mixed", "movies", "music", "musicvideos", "tvshows"}
_VERSION = re.compile(r"^v?[0-9][0-9A-Za-z]*(?:[._+-][0-9A-Za-z]+)*$")


class SnapshotReader(Protocol):
    def read_snapshot(self) -> object: ...


@dataclass(frozen=True, slots=True)
class ServiceInventory:
    service: str
    state: str
    version: str | None = None
    api_version: int | None = None
    resources: tuple[str, ...] = ()
    unsupported_resources: tuple[str, ...] = ()
    evidence: tuple[tuple[str, object], ...] = ()
    failure_code: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "service": self.service,
            "state": self.state,
            "version": self.version,
            "api_version": self.api_version,
            "resources": list(self.resources),
            "unsupported_resources": list(self.unsupported_resources),
            "evidence": {key: _json_value(value) for key, value in self.evidence},
            "failure_code": self.failure_code,
            "retryable": self.retryable,
        }
        return result


@dataclass(frozen=True, slots=True)
class StackInventory:
    services: tuple[ServiceInventory, ...]
    state: str
    schema: str = "arr-orchestrator.stack-inventory.v1"

    def to_dict(self) -> dict[str, object]:
        counts = {state: 0 for state in _STATES}
        for service in self.services:
            counts[service.state] += 1
        return {
            "schema": self.schema,
            "state": self.state,
            "summary": counts,
            "services": [service.to_dict() for service in self.services],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _json_value(value: object) -> object:
    if isinstance(value, tuple):
        if all(isinstance(item, tuple) and len(item) == 2 for item in value):
            return {str(key): _json_value(item) for key, item in value}
        return [_json_value(item) for item in value]
    return value


def _version(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64 or _VERSION.fullmatch(value) is None:
        raise ValueError("INVALID_VERSION")
    return value


def _api_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
        raise ValueError("INVALID_API_VERSION")
    return value


def _resources(service: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or len(value) > len(_RESOURCES[service]):
        raise ValueError("INVALID_RESOURCES")
    if len(set(value)) != len(value) or any(item not in _RESOURCES[service] for item in value):
        raise ValueError("INVALID_RESOURCES")
    return tuple(value)


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10_000:
        raise ValueError("INVALID_COUNT")
    return value


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("INVALID_BOOLEAN")
    return value


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple) or len(value) > 10_000:
        raise ValueError("INVALID_COLLECTION")
    return value


def _common(service: str, snapshot: object) -> tuple[str, int | None, tuple[str, ...]]:
    capabilities = snapshot.capabilities
    if service == "jellyfin":
        version = _version(capabilities.server_version)
        api_version = None
    else:
        version = _version(capabilities.application_version)
        api_version = _api_version(capabilities.api_version) if service in {"sonarr", "radarr", "prowlarr"} else None
    return version, api_version, _resources(service, capabilities.resources)


def _project(service: str, snapshot: object) -> ServiceInventory:
    version, api_version, resources = _common(service, snapshot)
    unsupported: tuple[str, ...] = ()
    evidence: tuple[tuple[str, object], ...]
    if service in {"sonarr", "radarr"}:
        roots = _tuple(snapshot.root_folders)
        clients = _tuple(snapshot.download_clients)
        profiles = _tuple(snapshot.quality_profiles)
        evidence = (
            ("root_folder_count", len(roots)),
            ("inaccessible_root_folder_count", sum(not _bool(item.accessible) for item in roots)),
            ("download_client_count", len(clients)),
            ("enabled_download_client_count", sum(_bool(item.enabled) for item in clients)),
            ("quality_profile_count", len(profiles)),
            ("queue_total", _count(snapshot.queue.total_records)),
        )
    elif service == "prowlarr":
        applications = _tuple(snapshot.applications)
        evidence = (
            ("application_count", len(applications)),
            ("indexer_total", _count(snapshot.indexers.total)),
            ("indexer_enabled", _count(snapshot.indexers.enabled)),
            ("indexer_rss_capable", _count(snapshot.indexers.rss_capable)),
            ("indexer_search_capable", _count(snapshot.indexers.search_capable)),
        )
    elif service == "qbittorrent":
        categories = _tuple(snapshot.categories)
        evidence = (
            ("webapi_version", _version(snapshot.capabilities.webapi_version)),
            ("category_count", len(categories)),
            ("queue_total", _count(snapshot.queue.total)),
        )
    else:
        libraries = _tuple(snapshot.libraries)
        collection_counts: dict[str, int] = {}
        location_count = 0
        refreshing = 0
        for library in libraries:
            collection_type = library.collection_type
            if collection_type not in _COLLECTION_TYPES:
                raise ValueError("INVALID_COLLECTION_TYPE")
            locations = _tuple(library.locations)
            collection_counts[collection_type] = collection_counts.get(collection_type, 0) + 1
            location_count += len(locations)
            refreshing += int(_bool(library.refreshing))
        refresh_supported = _bool(snapshot.refresh.supported)
        if not refresh_supported:
            unsupported = ("refresh_status",)
        evidence = (
            ("healthy", _bool(snapshot.health.healthy)),
            ("startup_complete", _bool(snapshot.health.startup_complete)),
            ("library_count", len(libraries)),
            ("location_count", location_count),
            ("refreshing_library_count", refreshing),
            ("collection_type_counts", tuple(sorted(collection_counts.items()))),
        )
    return ServiceInventory(
        service=service,
        state="partial" if unsupported else "available",
        version=version,
        api_version=api_version,
        resources=resources,
        unsupported_resources=unsupported,
        evidence=evidence,
    )


def _failure(service: str, error: BaseException) -> ServiceInventory:
    raw_code = getattr(error, "code", None)
    code = raw_code if isinstance(raw_code, str) and raw_code in _SAFE_FAILURES else "INVENTORY_READ_FAILED"
    if code in _UNREACHABLE:
        state = "unreachable"
    elif code in _UNSUPPORTED:
        state = "unsupported"
    else:
        state = "unknown"
    retryable = getattr(error, "retryable", False)
    return ServiceInventory(
        service=service,
        state=state,
        failure_code=code,
        retryable=retryable if isinstance(retryable, bool) else False,
    )


class StackInventoryBuilder:
    def __init__(self, readers: Mapping[str, SnapshotReader]):
        unknown = set(readers) - set(REQUIRED_SERVICES)
        if unknown:
            raise ValueError("UNKNOWN_SERVICE")
        self._readers = dict(readers)

    def read(self) -> StackInventory:
        services: list[ServiceInventory] = []
        for service in REQUIRED_SERVICES:
            reader = self._readers.get(service)
            if reader is None:
                services.append(ServiceInventory(service, "unknown", failure_code="ADAPTER_NOT_CONFIGURED"))
                continue
            try:
                snapshot = reader.read_snapshot()
            except Exception as error:
                services.append(_failure(service, error))
                continue
            try:
                services.append(_project(service, snapshot))
            except Exception:
                services.append(
                    ServiceInventory(
                        service,
                        "unknown",
                        failure_code="INVENTORY_PROJECTION_FAILED",
                    )
                )
        states = {service.state for service in services}
        if states == {"available"}:
            stack_state = "healthy"
        elif states == {"unknown"}:
            stack_state = "unknown"
        elif states == {"unreachable"}:
            stack_state = "unreachable"
        elif states == {"unsupported"}:
            stack_state = "unsupported"
        else:
            stack_state = "partial"
        return StackInventory(tuple(services), stack_state)
