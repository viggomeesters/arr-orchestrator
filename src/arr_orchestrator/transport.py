from __future__ import annotations

import json
import math
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import ServiceEndpoint
from .credentials import CredentialError, FileCredentialResolver, SecretValue


SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "cookie", "headers", "location", "password",
    "secret", "set-cookie", "token", "uri", "url",
}
RETRYABLE_STATUS = {429, 502, 503, 504}
HTTP_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
CREDENTIAL_HEADERS = {"Authorization": "Bearer ", "X-Api-Key": ""}


class TransportFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, attempts: int = 1):
        self.code = code
        self.retryable = retryable
        self.attempts = attempts
        super().__init__(code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"TransportFailure(code={self.code!r}, retryable={self.retryable!r}, attempts={self.attempts!r})"


@dataclass(frozen=True)
class TransportPolicy:
    deadline_seconds: float = 3.0
    max_response_bytes: int = 1_048_576
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.05
    tls_verify: bool = True

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.deadline_seconds)
            or self.deadline_seconds <= 0
            or self.max_response_bytes < 1
            or not 1 <= self.max_attempts <= 3
        ):
            raise ValueError("transport policy bounds are invalid")
        if not math.isfinite(self.retry_backoff_seconds) or self.retry_backoff_seconds < 0:
            raise ValueError("transport retry backoff is invalid")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class QbittorrentSession:
    __slots__ = ("__cookie",)

    def __init__(self, cookie: str):
        self.__cookie = cookie

    def _cookie_header(self) -> str:
        return self.__cookie

    def __repr__(self) -> str:
        return "QbittorrentSession('[REDACTED]')"

    __str__ = __repr__


def _default_opener(tls_verify: bool):
    context = ssl.create_default_context() if tls_verify else ssl._create_unverified_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )


def _contains_sensitive_key(value: Any, private_values: tuple[str, ...] = ()) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = re.sub(r"[^a-z0-9]", "", normalized)
            if (
                normalized in SENSITIVE_KEYS
                or normalized.endswith(("url", "uri"))
                or "privatekey" in compact
                or any(
                    part in normalized
                    for part in ("password", "secret", "token", "cookie", "authorization", "header")
                )
            ):
                return True
            if _contains_sensitive_key(item, private_values):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item, private_values) for item in value)
    elif isinstance(value, str):
        candidates = [value]
        for _ in range(3):
            decoded = urllib.parse.unquote(candidates[-1])
            if decoded == candidates[-1]:
                break
            candidates.append(decoded)
        for candidate in candidates:
            parsed = urllib.parse.urlsplit(candidate)
            upper = candidate.upper()
            if (
                parsed.scheme
                or parsed.netloc
                or any(private and private in candidate for private in private_values)
                or ("-----BEGIN " in upper and "-----" in upper)
            ):
                return True
    return False


def _safe_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise TransportFailure("ORIGIN_DENIED")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path) or "\\" in path:
        raise TransportFailure("ORIGIN_DENIED")
    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise TransportFailure("ORIGIN_DENIED")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or ".." in parsed.path.split("/"):
        raise TransportFailure("ORIGIN_DENIED")
    decoded_path = None
    try:
        decoded_path = urllib.parse.unquote_to_bytes(parsed.path).decode("utf-8")
    except UnicodeDecodeError:
        pass
    if decoded_path is None:
        raise TransportFailure("ORIGIN_DENIED")
    if (
        "\\" in decoded_path
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded_path)
        or ".." in decoded_path.split("/")
    ):
        raise TransportFailure("ORIGIN_DENIED")
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))


class ReadOnlyHttpTransport:
    def __init__(
        self,
        endpoint: ServiceEndpoint,
        resolver: FileCredentialResolver,
        *,
        policy: TransportPolicy | None = None,
        opener: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        credential_header: str = "Authorization",
        credential_prefix: str = "Bearer ",
    ):
        if (
            not isinstance(credential_header, str)
            or HTTP_HEADER_NAME.fullmatch(credential_header) is None
            or credential_header not in CREDENTIAL_HEADERS
        ):
            raise ValueError("credential header name is invalid")
        if (
            not isinstance(credential_prefix, str)
            or len(credential_prefix) > 32
            or any(ord(char) < 0x20 or ord(char) > 0x7E for char in credential_prefix)
        ):
            raise ValueError("credential header prefix is invalid")
        if credential_prefix != CREDENTIAL_HEADERS[credential_header]:
            raise ValueError("credential header scheme is invalid")
        self.endpoint = endpoint
        self.resolver = resolver
        self.policy = policy or TransportPolicy()
        self.opener = opener or _default_opener(self.policy.tls_verify)
        self.clock = clock
        self.sleeper = sleeper
        self.credential_header = credential_header
        self.credential_prefix = credential_prefix

    def get_json(self, path: str) -> dict[str, Any] | list[Any]:
        return self.request_json("GET", path)

    def request_json(self, method: str, path: str) -> dict[str, Any] | list[Any]:
        method = method.upper()
        if method not in {"GET", "HEAD"}:
            raise TransportFailure("MUTATION_DISABLED")
        safe_path = _safe_path(path)
        credential = None
        try:
            credential = self.resolver.resolve(self.endpoint.secret_ref)
        except CredentialError:
            pass
        if credential is None:
            raise TransportFailure("CREDENTIAL_INVALID")
        credential_text = credential.reveal()
        credential_is_ascii = True
        try:
            credential_text.encode("ascii")
        except UnicodeEncodeError:
            credential_is_ascii = False
        if not credential_is_ascii:
            raise TransportFailure("CREDENTIAL_INVALID")
        deadline = self.clock() + self.policy.deadline_seconds
        last_failure: TransportFailure | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt - 1)
            request = urllib.request.Request(
                self.endpoint.base_url + safe_path,
                headers={
                    "Accept": "application/json",
                    self.credential_header: f"{self.credential_prefix}{credential_text}",
                },
                method=method,
            )
            try:
                with self.opener.open(request, timeout=remaining) as response:
                    if self.clock() >= deadline:
                        raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt)
                    status = int(response.status)
                    if status in RETRYABLE_STATUS:
                        raise TransportFailure("SERVICE_UNREACHABLE", retryable=True, attempts=attempt)
                    if not 200 <= status < 300:
                        raise TransportFailure("HTTP_STATUS_INVALID", attempts=attempt)
                    if method == "HEAD":
                        if self.clock() >= deadline:
                            raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt)
                        return {}
                    content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise TransportFailure("CONTENT_TYPE_INVALID", attempts=attempt)
                    body = response.read(self.policy.max_response_bytes + 1)
                    if self.clock() >= deadline:
                        raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt)
                    if len(body) > self.policy.max_response_bytes:
                        raise TransportFailure("RESPONSE_TOO_LARGE", attempts=attempt)
                    parse_failed = False
                    try:
                        parsed = json.loads(body)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parse_failed = True
                        parsed = None
                    if parse_failed:
                        raise TransportFailure("JSON_INVALID", attempts=attempt)
                    if not isinstance(parsed, (dict, list)):
                        raise TransportFailure("JSON_INVALID", attempts=attempt)
                    if _contains_sensitive_key(parsed, (credential_text,)):
                        raise TransportFailure("PRIVATE_DATA_REDACTED", attempts=attempt)
                    if self.clock() >= deadline:
                        raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt)
                    return parsed
            except urllib.error.HTTPError as error:
                if 300 <= error.code < 400:
                    raise TransportFailure("REDIRECT_DENIED", attempts=attempt) from None
                if error.code in {401, 403}:
                    code = "AUTH_FAILED"
                elif error.code == 404:
                    code = "RESOURCE_NOT_FOUND"
                elif error.code in RETRYABLE_STATUS:
                    code = "SERVICE_UNREACHABLE"
                else:
                    code = "HTTP_STATUS_INVALID"
                last_failure = TransportFailure(
                    code,
                    retryable=error.code in RETRYABLE_STATUS,
                    attempts=attempt,
                )
            except (TimeoutError, socket.timeout):
                last_failure = TransportFailure("DEADLINE_EXCEEDED", retryable=True, attempts=attempt)
            except urllib.error.URLError as error:
                if isinstance(error.reason, ssl.SSLError):
                    last_failure = TransportFailure(
                        "TLS_VERIFICATION_FAILED", retryable=False, attempts=attempt
                    )
                else:
                    timed_out = isinstance(error.reason, (TimeoutError, socket.timeout))
                    last_failure = TransportFailure(
                        "DEADLINE_EXCEEDED" if timed_out else "SERVICE_UNREACHABLE",
                        retryable=True,
                        attempts=attempt,
                    )
            except ssl.SSLError:
                last_failure = TransportFailure(
                    "TLS_VERIFICATION_FAILED", retryable=False, attempts=attempt
                )
            except OSError:
                last_failure = TransportFailure(
                    "SERVICE_UNREACHABLE", retryable=True, attempts=attempt
                )
            except TransportFailure as error:
                last_failure = error
            if last_failure is None or not last_failure.retryable or attempt >= self.policy.max_attempts:
                raise last_failure or TransportFailure("SERVICE_UNREACHABLE", attempts=attempt)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TransportFailure("DEADLINE_EXCEEDED", attempts=attempt)
            self.sleeper(min(self.policy.retry_backoff_seconds, remaining))
        raise last_failure or TransportFailure("SERVICE_UNREACHABLE")


def authenticate_qbittorrent_session(
    endpoint: ServiceEndpoint,
    username: SecretValue,
    password_value: SecretValue,
    *,
    policy: TransportPolicy | None = None,
    opener: Any | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> QbittorrentSession:
    active_policy = policy or TransportPolicy(max_attempts=1)
    client = opener or _default_opener(active_policy.tls_verify)
    body = urllib.parse.urlencode(
        {"username": username.reveal(), "password": password_value.reveal()}
    ).encode("ascii")
    request = urllib.request.Request(
        endpoint.base_url + "/api/v2/auth/login",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/plain"},
        method="POST",
    )
    deadline = clock() + active_policy.deadline_seconds
    remaining = deadline - clock()
    if remaining <= 0:
        raise TransportFailure("DEADLINE_EXCEEDED")
    failure: TransportFailure | None = None
    try:
        with client.open(request, timeout=remaining) as response:
            if clock() >= deadline:
                raise TransportFailure("DEADLINE_EXCEEDED")
            status = int(response.status)
            response_body = response.read(4)
            if clock() >= deadline:
                raise TransportFailure("DEADLINE_EXCEEDED")
            if status != 200 or response_body != b"Ok.":
                raise TransportFailure("AUTH_FAILED")
            cookie = str(response.headers.get("Set-Cookie", "")).split(";", 1)[0]
            if not cookie.startswith("SID=") or len(cookie) <= 4 or any(char in cookie for char in "\r\n"):
                raise TransportFailure("AUTH_FAILED")
            if clock() >= deadline:
                raise TransportFailure("DEADLINE_EXCEEDED")
            return QbittorrentSession(cookie)
    except TransportFailure as error:
        failure = error
    except urllib.error.HTTPError:
        failure = TransportFailure("AUTH_FAILED")
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            failure = TransportFailure("TLS_VERIFICATION_FAILED")
        elif isinstance(error.reason, (TimeoutError, socket.timeout)):
            failure = TransportFailure("DEADLINE_EXCEEDED")
        else:
            failure = TransportFailure("AUTH_FAILED")
    except ssl.SSLError:
        failure = TransportFailure("TLS_VERIFICATION_FAILED")
    except (TimeoutError, socket.timeout):
        failure = TransportFailure("DEADLINE_EXCEEDED")
    except OSError:
        failure = TransportFailure("AUTH_FAILED")
    raise failure or TransportFailure("AUTH_FAILED")
