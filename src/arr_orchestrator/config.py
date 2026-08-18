from __future__ import annotations

import json
import ipaddress
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class EndpointConfigError(ValueError):
    """Runtime endpoint configuration is invalid or unsafe."""


SERVICE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SECRET_REF = re.compile(r"^file:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class ServiceEndpoint:
    service_id: str
    base_url: str
    secret_ref: str

    def __post_init__(self) -> None:
        normalized = _validated_values(self.service_id, self.base_url, self.secret_ref)
        object.__setattr__(self, "base_url", normalized)

    def __repr__(self) -> str:
        return f"ServiceEndpoint(service_id={self.service_id!r}, base_origin='[REDACTED]', secret_ref='[REFERENCE]')"


def _validated_values(service_id: str, base_url: str, secret_ref: str) -> str:
    if not isinstance(service_id, str) or not SERVICE_ID.fullmatch(service_id):
        raise EndpointConfigError("service endpoint identity is invalid")
    if not isinstance(base_url, str) or not isinstance(secret_ref, str):
        raise EndpointConfigError("service endpoint values are invalid")
    if base_url != base_url.strip() or any(ord(char) < 0x21 or ord(char) == 0x7F for char in base_url):
        raise EndpointConfigError("base_url must be one explicit HTTP origin")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise EndpointConfigError("base_url is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise EndpointConfigError("base_url must be one explicit HTTP origin")
    if ":" in hostname:
        raise EndpointConfigError("base_url hostname is invalid")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if (
            len(hostname) > 253
            or not hostname.isascii()
            or any(not HOST_LABEL.fullmatch(label) for label in hostname.split("."))
        ):
            raise EndpointConfigError("base_url hostname is invalid") from None
    authority = parsed.netloc
    if authority.startswith("["):
        port_text = authority.partition("]")[2]
        port_text = port_text[1:] if port_text.startswith(":") else ""
    else:
        port_text = authority.rpartition(":")[2] if ":" in authority else ""
    if port_text and len(port_text) > 1 and port_text.startswith("0"):
        raise EndpointConfigError("base_url port is not canonical")
    if port is not None and not 1 <= port <= 65535:
        raise EndpointConfigError("base_url port is invalid")
    if not SECRET_REF.fullmatch(secret_ref):
        raise EndpointConfigError("secret_ref must be one contained file reference")
    normalized = f"{parsed.scheme}://{hostname}"
    if port is not None:
        normalized += f":{port}"
    return normalized


def _validate_endpoint(service_id: str, raw: Any) -> ServiceEndpoint:
    if not isinstance(raw, dict) or set(raw) != {"base_url", "secret_ref"}:
        raise EndpointConfigError("service endpoint fields are invalid")
    return ServiceEndpoint(service_id, raw.get("base_url"), raw.get("secret_ref"))


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise EndpointConfigError("endpoint configuration path contains a symlink")


def _reject_repository_path(path: Path) -> None:
    resolved = path.resolve(strict=True)
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / ".git").exists():
            raise EndpointConfigError("endpoint configuration must live outside Git")


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EndpointConfigError("endpoint configuration contains duplicate members")
        result[key] = value
    return result


def _read_configuration(path: Path, *, max_bytes: int = 1_048_576) -> str:
    absolute = path.absolute()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_descriptor = os.open(absolute.anchor, directory_flags)
    try:
        for component in absolute.parent.parts[1:]:
            next_descriptor = os.open(component, directory_flags, dir_fd=directory_descriptor)
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(absolute.name, file_flags, dir_fd=directory_descriptor)
    finally:
        os.close(directory_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EndpointConfigError("endpoint configuration file is unavailable")
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(descriptor, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise EndpointConfigError("endpoint configuration file is too large")
    return raw.decode("utf-8")


def load_service_endpoints(path: Path) -> dict[str, ServiceEndpoint]:
    _reject_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise EndpointConfigError("endpoint configuration file is unavailable")
    _reject_repository_path(path)
    try:
        payload = json.loads(
            _read_configuration(path), object_pairs_hook=_reject_duplicate_members
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise EndpointConfigError("endpoint configuration is not valid JSON") from None
    if not isinstance(payload, dict) or set(payload) != {"schema", "services"}:
        raise EndpointConfigError("endpoint configuration envelope is invalid")
    if payload["schema"] != "arr-orchestrator.runtime-service-endpoints.v1":
        raise EndpointConfigError("endpoint configuration schema is unsupported")
    services = payload["services"]
    if not isinstance(services, dict) or not services:
        raise EndpointConfigError("endpoint configuration requires services")
    return {service_id: _validate_endpoint(service_id, raw) for service_id, raw in services.items()}
