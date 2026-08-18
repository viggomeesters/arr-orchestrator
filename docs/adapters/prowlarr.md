# Prowlarr read-only adapter

The Prowlarr adapter converts the API v1 surface into a small typed snapshot. It delegates origin policy, file-backed credentials, proxy and redirect refusal, TLS verification, deadlines, retries, response limits, typed failures, and redaction to `ReadOnlyHttpTransport`; it does not implement a private HTTP client.

## Authentication and API discovery

Use the fixed `X-Api-Key` credential profile:

```python
transport = ReadOnlyHttpTransport(
    endpoint,
    resolver,
    credential_header="X-Api-Key",
    credential_prefix="",
)
adapter = ProwlarrAdapter(transport)
```

The adapter probes `GET /api/v1/system/status` and requires the returned application identity to be Prowlarr. A discovery `404` becomes `UNSUPPORTED_API_VERSION`; authentication failures and missing later resources retain their transport failure codes.

## Normalized output

`read_snapshot()` performs exactly three API v1 `GET` requests:

- `/api/v1/system/status` — application identity, version, branch, runtime version, and OS name;
- `/api/v1/applications` — application identifier, display name, implementation, and sync level;
- `/api/v1/indexer` — aggregate totals for enabled, RSS-capable, and search-capable indexers plus protocol and privacy counts.

Application `fields`, base URLs, API keys, tags, provider messages, test commands, presets, and info links are excluded. Indexer names, definitions, URLs, fields, query values, provider messages, status payloads, application-profile identifiers, download-client identifiers, and history data are never persisted or returned. Indexers leave the adapter only as counts.

The response lists are bounded to 10,000 records each. Unsupported sync levels, protocols, privacy levels, malformed booleans, negative identifiers, oversized strings, and oversized lists fail closed.

## Typed failures

`ProwlarrAdapterFailure` exposes only a stable code and retryability. Transport failures are converted outside the caught exception context, preventing URLs, headers, credentials, response bodies, and lower-level exception text from remaining reachable through `__cause__` or `__context__`.

Adapter-specific codes include:

- `UNSUPPORTED_API_VERSION`
- `SERVICE_IDENTITY_INVALID`
- `RESPONSE_SHAPE_INVALID`
- `RESPONSE_BOUNDS_INVALID`

Safe transport codes such as `AUTH_FAILED`, `RESOURCE_NOT_FOUND`, `SERVICE_UNREACHABLE`, and `TLS_VERIFICATION_FAILED` remain truthful.

## Verification

Synthetic contract tests:

```bash
python3 -m unittest tests.adapters.test_prowlarr
```

Focused real-container proof:

```bash
python3 scripts/lab.py test adapter prowlarr
```

The live lane starts the digest-pinned Prowlarr `2.5.2.5491` image and an ephemeral non-root controller on one internal Docker network. It mounts one generated API key read-only, measures exactly three physical `GET` attempts through the shared transport, publishes no host ports, exposes no credential material, and removes only its marker- and project-labelled resources.
