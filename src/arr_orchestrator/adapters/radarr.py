from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..transport import TransportFailure


API_VERSION = 3
API_ROOT = f"/api/v{API_VERSION}"
QUEUE_PAGE_SIZE = 100
MAX_QUEUE_RECORDS = 10_000
MAX_QUEUE_PAGES = MAX_QUEUE_RECORDS // QUEUE_PAGE_SIZE
RESOURCES = (
    "system_status",
    "root_folders",
    "download_clients",
    "quality_profiles",
    "queue_summary",
)
DOWNLOAD_CLIENT_FIELDS = (
    "id",
    "name",
    "implementation",
    "protocol",
    "enable",
    "priority",
    "removeCompletedDownloads",
    "removeFailedDownloads",
)


class JsonTransport(Protocol):
    def get_json(self, path: str) -> dict[str, Any] | list[Any]: ...

    def get_json_list_fields(
        self, path: str, fields: tuple[str, ...]
    ) -> list[dict[str, Any]]: ...


class RadarrAdapterFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"RadarrAdapterFailure(code={self.code!r}, retryable={self.retryable!r})"


@dataclass(frozen=True)
class RadarrCapabilities:
    api_version: int
    application_version: str
    branch: str
    resources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "application_version": self.application_version,
            "branch": self.branch,
            "resources": list(self.resources),
        }


@dataclass(frozen=True)
class RadarrSystemStatus:
    application_version: str
    branch: str
    runtime_version: str
    os_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_version": self.application_version,
            "branch": self.branch,
            "runtime_version": self.runtime_version,
            "os_name": self.os_name,
        }


@dataclass(frozen=True)
class RadarrRootFolder:
    id: int
    path: str
    accessible: bool
    free_space: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "accessible": self.accessible,
            "free_space": self.free_space,
        }


@dataclass(frozen=True)
class RadarrDownloadClient:
    id: int
    name: str
    implementation: str
    protocol: str
    enabled: bool
    priority: int
    remove_completed_downloads: bool
    remove_failed_downloads: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "implementation": self.implementation,
            "protocol": self.protocol,
            "enabled": self.enabled,
            "priority": self.priority,
            "remove_completed_downloads": self.remove_completed_downloads,
            "remove_failed_downloads": self.remove_failed_downloads,
        }


@dataclass(frozen=True)
class RadarrQualityProfile:
    id: int
    name: str
    upgrade_allowed: bool
    cutoff: int
    min_format_score: int
    cutoff_format_score: int
    min_upgrade_format_score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "upgrade_allowed": self.upgrade_allowed,
            "cutoff": self.cutoff,
            "min_format_score": self.min_format_score,
            "cutoff_format_score": self.cutoff_format_score,
            "min_upgrade_format_score": self.min_upgrade_format_score,
        }


@dataclass(frozen=True)
class RadarrQueueSummary:
    total_records: int
    status_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "status_counts": dict(self.status_counts),
        }


@dataclass(frozen=True)
class RadarrSnapshot:
    capabilities: RadarrCapabilities
    system_status: RadarrSystemStatus
    root_folders: tuple[RadarrRootFolder, ...]
    download_clients: tuple[RadarrDownloadClient, ...]
    quality_profiles: tuple[RadarrQualityProfile, ...]
    queue: RadarrQueueSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": self.capabilities.to_dict(),
            "system_status": self.system_status.to_dict(),
            "root_folders": [item.to_dict() for item in self.root_folders],
            "download_clients": [item.to_dict() for item in self.download_clients],
            "quality_profiles": [item.to_dict() for item in self.quality_profiles],
            "queue": self.queue.to_dict(),
        }


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > 512:
        raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _integer(payload: dict[str, Any], key: str, *, minimum: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _boolean(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _sequence(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


class RadarrAdapter:
    def __init__(self, transport: JsonTransport):
        self.transport = transport

    def _read(self, path: str, *, version_probe: bool = False) -> dict[str, Any] | list[Any]:
        failure: RadarrAdapterFailure | None = None
        payload: dict[str, Any] | list[Any] | None = None
        try:
            payload = self.transport.get_json(path)
        except TransportFailure as error:
            code = (
                "UNSUPPORTED_API_VERSION"
                if version_probe and error.code == "RESOURCE_NOT_FOUND"
                else error.code
            )
            failure = RadarrAdapterFailure(code, retryable=error.retryable)
        if failure is not None:
            raise failure
        if payload is None:
            raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
        return payload

    def _status(self) -> tuple[RadarrCapabilities, RadarrSystemStatus]:
        payload = _mapping(self._read(f"{API_ROOT}/system/status", version_probe=True))
        if _string(payload, "appName").lower() != "radarr":
            raise RadarrAdapterFailure("SERVICE_IDENTITY_INVALID")
        application_version = _string(payload, "version")
        branch = _string(payload, "branch")
        status = RadarrSystemStatus(
            application_version=application_version,
            branch=branch,
            runtime_version=_string(payload, "runtimeVersion"),
            os_name=_string(payload, "osName"),
        )
        capabilities = RadarrCapabilities(
            api_version=API_VERSION,
            application_version=application_version,
            branch=branch,
            resources=RESOURCES,
        )
        return capabilities, status

    def discover_capabilities(self) -> RadarrCapabilities:
        capabilities, _ = self._status()
        return capabilities

    def _root_folders(self) -> tuple[RadarrRootFolder, ...]:
        output = []
        for raw in _sequence(self._read(f"{API_ROOT}/rootfolder")):
            item = _mapping(raw)
            free_space = item.get("freeSpace")
            if free_space is not None and (
                isinstance(free_space, bool) or not isinstance(free_space, int) or free_space < 0
            ):
                raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
            output.append(
                RadarrRootFolder(
                    id=_integer(item, "id"),
                    path=_string(item, "path"),
                    accessible=_boolean(item, "accessible"),
                    free_space=free_space,
                )
            )
        return tuple(output)

    def _download_clients(self) -> tuple[RadarrDownloadClient, ...]:
        output = []
        failure: RadarrAdapterFailure | None = None
        payload: list[dict[str, Any]] | None = None
        try:
            payload = self.transport.get_json_list_fields(
                f"{API_ROOT}/downloadclient", DOWNLOAD_CLIENT_FIELDS
            )
        except TransportFailure as error:
            failure = RadarrAdapterFailure(error.code, retryable=error.retryable)
        if failure is not None:
            raise failure
        if payload is None:
            raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
        for raw in _sequence(payload):
            item = _mapping(raw)
            output.append(
                RadarrDownloadClient(
                    id=_integer(item, "id"),
                    name=_string(item, "name"),
                    implementation=_string(item, "implementation"),
                    protocol=_string(item, "protocol"),
                    enabled=_boolean(item, "enable"),
                    priority=_integer(item, "priority"),
                    remove_completed_downloads=_boolean(item, "removeCompletedDownloads"),
                    remove_failed_downloads=_boolean(item, "removeFailedDownloads"),
                )
            )
        return tuple(output)

    def _quality_profiles(self) -> tuple[RadarrQualityProfile, ...]:
        output = []
        for raw in _sequence(self._read(f"{API_ROOT}/qualityprofile")):
            item = _mapping(raw)
            output.append(
                RadarrQualityProfile(
                    id=_integer(item, "id"),
                    name=_string(item, "name"),
                    upgrade_allowed=_boolean(item, "upgradeAllowed"),
                    cutoff=_integer(item, "cutoff"),
                    min_format_score=_integer(item, "minFormatScore"),
                    cutoff_format_score=_integer(item, "cutoffFormatScore"),
                    min_upgrade_format_score=_integer(item, "minUpgradeFormatScore"),
                )
            )
        return tuple(output)

    def _queue_summary(self) -> RadarrQueueSummary:
        status_counts: dict[str, int] = {}
        expected_total: int | None = None
        collected = 0
        for page in range(1, MAX_QUEUE_PAGES + 1):
            path = (
                f"{API_ROOT}/queue?page={page}&pageSize={QUEUE_PAGE_SIZE}"
                "&sortKey=timeleft&sortDirection=ascending"
            )
            payload = _mapping(self._read(path))
            observed_page = _integer(payload, "page", minimum=1)
            observed_page_size = _integer(payload, "pageSize", minimum=1)
            total = _integer(payload, "totalRecords")
            records = _sequence(payload.get("records"))
            if (
                observed_page != page
                or observed_page_size != QUEUE_PAGE_SIZE
                or len(records) > QUEUE_PAGE_SIZE
                or total > MAX_QUEUE_RECORDS
            ):
                raise RadarrAdapterFailure("QUEUE_BOUNDS_INVALID")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise RadarrAdapterFailure("QUEUE_PAGINATION_INVALID")
            for raw in records:
                item = _mapping(raw)
                status = _string(item, "status").lower()
                if len(status) > 64:
                    raise RadarrAdapterFailure("RESPONSE_SHAPE_INVALID")
                status_counts[status] = status_counts.get(status, 0) + 1
            collected += len(records)
            if collected == total:
                return RadarrQueueSummary(total, tuple(sorted(status_counts.items())))
            if collected > total or not records:
                raise RadarrAdapterFailure("QUEUE_PAGINATION_INVALID")
        raise RadarrAdapterFailure("QUEUE_BOUNDS_INVALID")

    def read_snapshot(self) -> RadarrSnapshot:
        capabilities, system_status = self._status()
        return RadarrSnapshot(
            capabilities=capabilities,
            system_status=system_status,
            root_folders=self._root_folders(),
            download_clients=self._download_clients(),
            quality_profiles=self._quality_profiles(),
            queue=self._queue_summary(),
        )
