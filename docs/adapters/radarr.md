# Radarr read-only adapter

The Radarr adapter converts the service-specific API v3 surface into a small, typed, read-only snapshot. It uses `ReadOnlyHttpTransport` for origin policy, file-backed credential resolution, proxy and redirect refusal, TLS verification, deadlines, retries, response limits, typed failures, and redaction. It does not implement a separate HTTP client.

## Authentication and API discovery

Construct the shared transport with the Radarr credential profile:

```python
transport = ReadOnlyHttpTransport(
    endpoint,
    resolver,
    credential_header="X-Api-Key",
    credential_prefix="",
)
adapter = RadarrAdapter(transport)
```

The transport accepts only its two fixed credential profiles: bearer authorization and the `X-Api-Key` header. Arbitrary credential headers and mismatched schemes are rejected before network I/O.

The adapter probes `GET /api/v3/system/status` and verifies the returned application identity. A missing v3 discovery resource becomes `UNSUPPORTED_API_VERSION`; authentication failures remain `AUTH_FAILED`, and missing later resources remain `RESOURCE_NOT_FOUND`.

## Normalized output

`discover_capabilities()` returns:

- API version;
- Radarr application version and branch;
- the adapter's supported read resources.

`read_snapshot()` performs only API v3 `GET` requests and returns allowlisted models for:

- system status without startup paths, URLs, or raw runtime metadata;
- root folders: identifier, path, accessibility, and free space;
- download clients: identity, implementation, protocol, enabled state, priority, and removal policy;
- quality profiles: identity, upgrade/cutoff settings, and format-score thresholds;
- queue summary: total record count and status counts.

Provider `fields`, queue titles, movie identifiers, download identifiers, output paths, request metadata, response headers, and raw service payloads are never included in the normalized result. Model representations contain only normalized fields.

## Queue bounds

Queue reads use deterministic pages of 100 records. The adapter refuses:

- more than 10,000 records or 100 pages;
- response page sizes other than 100;
- more than 100 records in one page;
- inconsistent `totalRecords` values;
- wrong page identities;
- empty intermediate pages;
- malformed status or pagination fields.

No queue item metadata leaves the adapter; only aggregate counts are returned.

## Typed failures

`RadarrAdapterFailure` contains only a stable code and retryability. Transport exceptions are converted outside their exception context, so URLs, headers, credentials, response bodies, and lower-level exception text are not reachable through `__cause__` or `__context__`.

Adapter-specific codes include:

- `UNSUPPORTED_API_VERSION`
- `SERVICE_IDENTITY_INVALID`
- `RESPONSE_SHAPE_INVALID`
- `QUEUE_BOUNDS_INVALID`
- `QUEUE_PAGINATION_INVALID`

Safe transport codes such as `AUTH_FAILED`, `RESOURCE_NOT_FOUND`, `SERVICE_UNREACHABLE`, and `TLS_VERIFICATION_FAILED` are preserved.

## Verification

Synthetic contract tests:

```bash
python3 -m unittest tests.adapters.test_radarr
```

Focused real-container proof:

```bash
python3 scripts/lab.py test adapter radarr
```

The live lane starts the digest-pinned Radarr `6.3.0.10514` image and an ephemeral non-root controller on one internal Docker network. It mounts one generated credential read-only, performs five API `GET` requests through the shared transport, publishes no host ports, exposes no credential material, and removes only its marker- and project-labelled resources.
