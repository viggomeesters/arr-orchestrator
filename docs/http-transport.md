# Shared read-only HTTP transport

The shared transport is the only generic HTTP boundary used by service adapters. It is intentionally narrow: it resolves one explicit service origin, reads one file-backed credential, performs bounded JSON `GET` requests, and returns parsed service data or a typed redacted failure.

## Runtime endpoint configuration

Runtime endpoint configuration is external state. It must live outside the repository and validate against `schemas/runtime/service-endpoints.schema.json`.

```json
{
  "schema": "arr-orchestrator.runtime-service-endpoints.v1",
  "services": {
    "sonarr": {
      "base_url": "https://sonarr.internal.example:8989",
      "secret_ref": "file:sonarr-api-key"
    }
  }
}
```

Rules:

- `base_url` is one explicit `http` or `https` origin using a DNS name or IPv4 address. User information, IPv6 literals, paths, queries, and fragments are rejected in v1.
- `secret_ref` is a contained single-file reference. Secret values never appear in endpoint configuration.
- Unknown fields are rejected.
- Repository-contained configuration and symlinked path components are rejected.
- `ServiceEndpoint` validates the same rules even when constructed directly rather than loaded from JSON.

`http` origins exist for the isolated synthetic Docker lab. Real deployments should use `https`; certificate verification is enabled by default.

## Credential files

`FileCredentialResolver` resolves only `file:<name>` inside its configured trusted root. It refuses:

- absolute paths, traversal, nested paths, and unsupported reference schemes;
- symlinked root, parent, or file components;
- non-regular files and multiply hard-linked files;
- files owned by an unexpected UID;
- modes other than `0600`;
- oversized, empty, non-UTF-8, multiline, control-character, or whitespace-padded values.

One conventional final LF is accepted and removed. The returned `SecretValue` is opaque in `str()` and `repr()`; code must explicitly call `reveal()` at the final request-construction boundary.

## Request policy

`ReadOnlyHttpTransport` supports JSON `GET` and data-free `HEAD`. A successful `HEAD` returns an empty object and never exposes response headers. `POST`, `PUT`, `PATCH`, `DELETE`, and other methods fail with `MUTATION_DISABLED` before network I/O.

Credential placement is restricted to two fixed profiles: bearer authorization and the `X-Api-Key` header. Header names and prefixes are validated at construction. Arbitrary headers such as `Host` or `Cookie`, mismatched schemes, and control characters are rejected before credential resolution or network I/O.

Each request enforces:

- the endpoint's exact configured origin;
- an absolute path on that origin, with cross-origin, scheme-relative, fragment, and traversal forms rejected;
- redirect refusal, including redirects to the configured origin;
- explicit refusal of ambient HTTP and HTTPS proxies;
- TLS certificate verification by default;
- one finite wall-clock deadline shared by every attempt, response read, parse, and backoff;
- a bounded response size;
- JSON media type and valid object-or-array JSON;
- rejection of parsed private metadata such as credentials, cookies, headers, tokens, or URLs.

The transport does not return request URLs, request/response headers, cookies, credentials, or raw response bytes.

## Retry policy

Retries are conservative and deterministic:

- only read requests can enter retry handling;
- at most three attempts are configurable;
- retryable HTTP statuses are `429`, `502`, `503`, and `504`;
- classified connection failures and timeouts are retryable while the shared deadline remains;
- TLS certificate and handshake failures are typed and never retried;
- schema, content-type, size, redirect, policy, and ordinary non-2xx failures are not retried;
- authentication is never automatically retried.

Service adapters remain responsible for API-version validation, endpoint-specific response schemas, pagination, and domain findings such as stale readback.

## Typed failures

Failures expose only a stable code, retryability, and attempt count. Their string and representation contain no private transport material.

Current codes include:

- `MUTATION_DISABLED`
- `ORIGIN_DENIED`
- `REDIRECT_DENIED`
- `SERVICE_UNREACHABLE`
- `TLS_VERIFICATION_FAILED`
- `CREDENTIAL_INVALID`
- `DEADLINE_EXCEEDED`
- `HTTP_STATUS_INVALID`
- `RESOURCE_NOT_FOUND`
- `CONTENT_TYPE_INVALID`
- `RESPONSE_TOO_LARGE`
- `JSON_INVALID`
- `PRIVATE_DATA_REDACTED`
- `AUTH_FAILED`

HTTP `401` and `403` become `AUTH_FAILED`; `404` becomes `RESOURCE_NOT_FOUND`. This lets adapters distinguish a missing versioned API resource from an authentication failure without exposing response bodies, reason phrases, URLs, or headers.

## qBittorrent authentication exception

qBittorrent session creation is isolated in `authenticate_qbittorrent_session`. It permits exactly one `POST /api/v2/auth/login`, never retries it automatically, enforces the same finite wall-clock deadline across open and response read, accepts only the expected success response, and returns an opaque `QbittorrentSession`. Generic callers cannot use that function to issue arbitrary mutations.

## Verification

Run the focused tests:

```bash
python3 -m unittest discover -s tests/transport -p 'test_*.py'
python3 -m unittest tests.integration.test_transport_fault_api
```

Run the isolated Docker proof:

```bash
python3 scripts/lab.py test transport
```

The live suite starts only the private `fault-api` and an ephemeral non-root controller. It proves a healthy read, typed unavailable/timeout/malformed-JSON failures, service-specific unsupported-version and stale-readback payload handoff, zero published ports, credential-output redaction, and bounded resource cleanup.
