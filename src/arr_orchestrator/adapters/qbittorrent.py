from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol

from arr_orchestrator.transport import TransportFailure

MAX_ITEMS = 10_000
VERSION = re.compile(r"^v?[0-9]+(?:\.[0-9]+){1,3}(?:_[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)?$")


class QbittorrentTransport(Protocol):
    def get_text(self, path: str) -> str: ...
    def get_json(self, path: str) -> dict[str, object] | list[object]: ...


class QbittorrentAdapterFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class QbittorrentCapabilities:
    application_version: str
    webapi_version: str
    resources: tuple[str, ...] = ("categories", "queue")
    def to_dict(self): return {"application_version": self.application_version, "webapi_version": self.webapi_version, "resources": list(self.resources)}


@dataclass(frozen=True)
class QbittorrentCategory:
    name: str
    save_path: str
    def to_dict(self): return {"name": self.name, "save_path": self.save_path}


@dataclass(frozen=True)
class QbittorrentQueueSummary:
    total: int
    state_counts: tuple[tuple[str, int], ...]
    category_counts: tuple[tuple[str, int], ...]
    def to_dict(self): return {"total": self.total, "state_counts": dict(self.state_counts), "category_counts": dict(self.category_counts)}


@dataclass(frozen=True)
class QbittorrentSnapshot:
    capabilities: QbittorrentCapabilities
    categories: tuple[QbittorrentCategory, ...]
    queue: QbittorrentQueueSummary
    def to_dict(self): return {"capabilities": self.capabilities.to_dict(), "categories": [x.to_dict() for x in self.categories], "queue": self.queue.to_dict()}


def _raise(code: str, retryable: bool = False):
    raise QbittorrentAdapterFailure(code, retryable=retryable)


def _map(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping): raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


def _string(value: object, *, allow_empty: bool = False, path: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 512
        or value != value.strip()
        or (not allow_empty and not value)
    ):
        raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value) or (
        path and (not value.startswith("/") or value.startswith("//"))
    ):
        raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
    return value


class QbittorrentAdapter:
    def __init__(self, transport: QbittorrentTransport): self.transport = transport

    def _text(self, path: str) -> str:
        failure = None
        try: return self.transport.get_text(path)
        except TransportFailure as error: failure = (error.code, error.retryable)
        return _raise(*failure)

    def _json(self, path: str):
        failure = None
        try: return self.transport.get_json(path)
        except TransportFailure as error: failure = (error.code, error.retryable)
        return _raise(*failure)

    def discover_capabilities(self) -> QbittorrentCapabilities:
        app = self._text("/api/v2/app/version")
        api = self._text("/api/v2/app/webapiVersion")
        if not VERSION.fullmatch(app) or not VERSION.fullmatch(api):
            raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
        return QbittorrentCapabilities(app, api)

    def _categories(self) -> tuple[QbittorrentCategory, ...]:
        raw = _map(self._json("/api/v2/torrents/categories"))
        if len(raw) > MAX_ITEMS: raise QbittorrentAdapterFailure("RESPONSE_BOUNDS_INVALID")
        result = []
        for key, value in raw.items():
            item = _map(value)
            name = _string(item.get("name"))
            if name != key: raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
            result.append(QbittorrentCategory(name, _string(item.get("savePath"), path=True)))
        return tuple(sorted(result, key=lambda x: x.name))

    def _queue(self) -> QbittorrentQueueSummary:
        payload = _map(self._json("/api/v2/sync/maindata?rid=0"))
        if "torrents" not in payload:
            rid = payload.get("rid")
            if (
                isinstance(rid, bool)
                or not isinstance(rid, int)
                or rid < 0
                or payload.get("full_update") is not True
                or not isinstance(payload.get("server_state"), Mapping)
            ):
                raise QbittorrentAdapterFailure("RESPONSE_SHAPE_INVALID")
            torrents: Mapping[str, object] = {}
        else:
            torrents = _map(payload["torrents"])
        if len(torrents) > MAX_ITEMS: raise QbittorrentAdapterFailure("RESPONSE_BOUNDS_INVALID")
        states: dict[str, int] = {}; categories: dict[str, int] = {}
        for raw in torrents.values():
            item = _map(raw)
            state = _string(item.get("state")); category = _string(item.get("category", ""), allow_empty=True)
            states[state] = states.get(state, 0) + 1
            categories[category] = categories.get(category, 0) + 1
        return QbittorrentQueueSummary(len(torrents), tuple(sorted(states.items())), tuple(sorted(categories.items())))

    def read_snapshot(self) -> QbittorrentSnapshot:
        return QbittorrentSnapshot(self.discover_capabilities(), self._categories(), self._queue())
