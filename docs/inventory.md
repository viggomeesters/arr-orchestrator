# Stack inventory

The stack inventory combines the five read-only service adapters into one immutable, deterministic, privacy-safe read model.

## Required services

Every inventory contains exactly one row for each expected service, in this order:

1. Sonarr
2. Radarr
3. Prowlarr
4. qBittorrent
5. Jellyfin

A missing reader is represented as `unknown`; it is never omitted and never treated as healthy.

## Stack states

- `healthy`: all five services are `available`.
- `partial`: service states are mixed, including an available service with any incomplete service or a mixture of different non-healthy states.
- `unreachable`: every service is unreachable.
- `unsupported`: every service is unsupported.
- `unknown`: no service has enough evidence for any stronger state.

Any incomplete service prevents an all-green stack claim.

## Service states

- `available`: the complete read-only snapshot was read and normalized.
- `partial`: the service is reachable, but a declared capability is unavailable. Jellyfin refresh readback is the current example.
- `unreachable`: transport evidence reports service unavailability, a deadline failure, or TLS verification failure.
- `unsupported`: the API version or required capability is unsupported.
- `unknown`: credentials, response validation, projection, or another safe read boundary prevented a stronger conclusion.

Known internal adapter and transport failure codes are preserved. Arbitrary exception codes and all exception messages are replaced with `INVENTORY_READ_FAILED`. Failures while projecting an already-returned snapshot are reported separately as `INVENTORY_PROJECTION_FAILED`; they cannot impersonate an adapter or authentication failure. Process-control exceptions are not swallowed.

## Privacy boundary

Inventory construction never serializes adapter snapshots directly and never calls their `to_dict()` methods. It projects only fixed fields:

- validated application and API versions;
- allowlisted resource identifiers;
- aggregate counts and booleans.

The public inventory excludes provider payloads, server names, application names, library names, item identities, task identities, paths, origins, credentials, tokens, cookies, headers, and exception text.

Prowlarr application and indexer responses contain sensitive provider fields. The shared transport therefore projects each list item to an exact endpoint-specific ordered fieldset before sensitive-key scanning:

- `/api/v1/applications`: `id`, `name`, `implementation`, `syncLevel`;
- `/api/v1/indexer`: `protocol`, `privacy`, `enable`, `supportsRss`, `supportsSearch`.

Raw or semantically equivalent reads of those endpoints fail with `FIELD_PROJECTION_REQUIRED` before credential resolution or opener use.

## Determinism

`StackInventory.to_json()` uses the versioned schema `arr-orchestrator.stack-inventory.v1`, fixed service ordering, sorted evidence keys, each adapter's fixed validated resource order, and compact sorted-key JSON.

## Lab verification

Run:

```bash
python3 scripts/lab.py test inventory
```

The digest-pinned lane:

1. starts and bootstraps all five services on the internal lab network;
2. reads one healthy inventory through the real adapters;
3. verifies that every physical adapter request is `GET`;
4. stops only Prowlarr under marker- and project-bound authority;
5. reads a second inventory and requires stack state `partial`, Prowlarr `unreachable`, and the other four services `available`;
6. checks inventory output against all credential values inside the credential-owning controller;
7. verifies zero published host ports and project-bound cleanup of containers, networks, volumes, and local images.
