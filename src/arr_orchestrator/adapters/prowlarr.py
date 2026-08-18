from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from arr_orchestrator.transport import TransportFailure

API_VERSION = 1
MAX_ITEMS = 10_000
SUPPORTED_RESOURCES = ("applications", "indexers", "system_status")
SYNC_LEVELS = {"disabled", "addOnly", "fullSync"}
PROTOCOLS = {"unknown", "usenet", "torrent"}
PRIVACY_LEVELS = {"public", "semiPrivate", "private"}


class JsonTransport(Protocol):
    def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, str | int | bool] | None = None,
    ) -> object: ...


class ProwlarrAdapterFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ProwlarrCapabilities:
    api_version: int
    application_version: str
    branch: str
    resources: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "api_version": self.api_version,
            "application_version": self.application_version,
            "branch": self.branch,
            "resources": list(self.resources),
        }


@dataclass(frozen=True)
class ProwlarrSystemStatus:
    application: str
    version: str
    branch: str
    runtime_version: str
    os_name: str

    def to_dict(self) -> dict[str, object]:
        return {
            "application": self.application,
            "version": self.version,
            "branch": self.branch,
            "runtime_version": self.runtime_version,
            "os_name": self.os_name,
        }


@dataclass(frozen=True)
class ProwlarrApplication:
    identifier: int
    name: str
    implementation: str
    sync_level: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            "name": self.name,
            "implementation": self.implementation,
            "sync_level": self.sync_level,
        }


@dataclass(frozen=True)
class ProwlarrIndexerSummary:
    total: int
    enabled: int
    rss_capable: int
    search_capable: int
    protocol_counts: tuple[tuple[str, int], ...]
    privacy_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "enabled": self.enabled,
            "rss_capable": self.rss_capable,
            "search_capable": self.search_capable,
            "protocol_counts": dict(self.protocol_counts),
            "privacy_counts": dict(self.privacy_counts),
        }


@dataclass(frozen=True)
class ProwlarrSnapshot:
    capabilities: ProwlarrCapabilities
    system_status: ProwlarrSystemStatus
    applications: tuple[ProwlarrApplication, ...]
    indexers: ProwlarrIndexerSummary

    def to_dict(self) -> dict[str, object]:
        return {
            "capabilities": self.capabilities.to_dict(),
            "system_status": self.system_status.to_dict(),
            "applications": [item.to_dict() for item in self.applications],
            "indexers": self.indexers.to_dict(),
        }


def _raise_adapter_failure(code: str, retryable: bool = False) -> Any:
    raise ProwlarrAdapterFailure(code, retryable=retryable)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    if len(value) > MAX_ITEMS:
        raise ProwlarrAdapterFailure("RESPONSE_BOUNDS_INVALID")
    return value


def _string(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _optional_string(item: Mapping[str, object], key: str) -> str:
    value = item.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 256:
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _integer(item: Mapping[str, object], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _boolean(item: Mapping[str, object], key: str) -> bool:
    value = item.get(key)
    if not isinstance(value, bool):
        raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


class ProwlarrAdapter:
    def __init__(self, transport: JsonTransport) -> None:
        self.transport = transport

    def _get_json(self, path: str) -> object:
        failure: tuple[str, bool] | None = None
        try:
            return self.transport.get_json(path)
        except TransportFailure as error:
            failure = (error.code, error.retryable)
        assert failure is not None
        return _raise_adapter_failure(*failure)

    def _discover_json(self) -> object:
        failure: tuple[str, bool] | None = None
        try:
            return self.transport.get_json("/api/v1/system/status")
        except TransportFailure as error:
            code = "UNSUPPORTED_API_VERSION" if error.code == "RESOURCE_NOT_FOUND" else error.code
            failure = (code, error.retryable)
        assert failure is not None
        return _raise_adapter_failure(*failure)

    def _status(self) -> tuple[ProwlarrCapabilities, ProwlarrSystemStatus]:
        item = _mapping(self._discover_json())
        application = _string(item, "appName")
        if application.lower() != "prowlarr":
            raise ProwlarrAdapterFailure("SERVICE_IDENTITY_INVALID")
        version = _string(item, "version")
        branch = _string(item, "branch")
        capabilities = ProwlarrCapabilities(API_VERSION, version, branch, SUPPORTED_RESOURCES)
        status = ProwlarrSystemStatus(
            application=application,
            version=version,
            branch=branch,
            runtime_version=_optional_string(item, "runtimeVersion"),
            os_name=_optional_string(item, "osName"),
        )
        return capabilities, status

    def discover_capabilities(self) -> ProwlarrCapabilities:
        capabilities, _ = self._status()
        return capabilities

    def _applications(self) -> tuple[ProwlarrApplication, ...]:
        applications: list[ProwlarrApplication] = []
        for raw in _list(self._get_json("/api/v1/applications")):
            item = _mapping(raw)
            sync_level = _string(item, "syncLevel")
            if sync_level not in SYNC_LEVELS:
                raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
            applications.append(
                ProwlarrApplication(
                    identifier=_integer(item, "id"),
                    name=_string(item, "name"),
                    implementation=_string(item, "implementation"),
                    sync_level=sync_level,
                )
            )
        return tuple(applications)

    def _indexers(self) -> ProwlarrIndexerSummary:
        values = _list(self._get_json("/api/v1/indexer"))
        enabled = 0
        rss_capable = 0
        search_capable = 0
        protocol_counts: dict[str, int] = {}
        privacy_counts: dict[str, int] = {}
        for raw in values:
            item = _mapping(raw)
            protocol = _string(item, "protocol")
            privacy = _string(item, "privacy")
            if protocol not in PROTOCOLS or privacy not in PRIVACY_LEVELS:
                raise ProwlarrAdapterFailure("RESPONSE_SHAPE_INVALID")
            enabled += int(_boolean(item, "enable"))
            rss_capable += int(_boolean(item, "supportsRss"))
            search_capable += int(_boolean(item, "supportsSearch"))
            protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1
            privacy_counts[privacy] = privacy_counts.get(privacy, 0) + 1
        return ProwlarrIndexerSummary(
            total=len(values),
            enabled=enabled,
            rss_capable=rss_capable,
            search_capable=search_capable,
            protocol_counts=tuple(sorted(protocol_counts.items())),
            privacy_counts=tuple(sorted(privacy_counts.items())),
        )

    def read_snapshot(self) -> ProwlarrSnapshot:
        capabilities, status = self._status()
        return ProwlarrSnapshot(
            capabilities=capabilities,
            system_status=status,
            applications=self._applications(),
            indexers=self._indexers(),
        )
