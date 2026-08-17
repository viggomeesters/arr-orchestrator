# Disposable local lab contract

The local lab is a synthetic, resettable integration environment for Sonarr, Radarr, Prowlarr, qBittorrent, Jellyfin, the orchestration controller, an isolated `arrctl` runner, and repository-owned dependency doubles. It is not a development shortcut around the production safety model; it is where that model is proved before any live deployment is inspected.

## Contract artifacts

The lab is defined by four Draft 2020-12 JSON Schemas:

- `schemas/lab-manifest.schema.json` — lab identity, service set, runtime-root reference, isolated network, reset boundary, and supported scenarios;
- `schemas/lab-images-lock.schema.json` — exact image platform, immutable index/manifest/config digests, application version, provenance, license, and attestation limitation;
- `schemas/lab-readiness.schema.json` — separate `process_alive`, `api_live`, `api_ready`, and `baseline_verified` states with explicit owners and bounded polling;
- `schemas/lab-security-matrix.schema.json` — per-container UID, capabilities, privilege, root-filesystem, mount, seccomp, PID, memory, CPU, and exception policy.

All objects reject unknown fields. Contract version `1.0.0` is intentionally narrow; incompatible changes require a reviewed schema-version update.

## Runtime identity and storage

A lab ID matches `^[a-z][a-z0-9-]{2,31}$`. The Compose project is `arr-orchestrator-<lab-id>` and its application network is `${COMPOSE_PROJECT_NAME}_private`.

The manifest stores a portable root reference:

```text
xdg-data:arr-orchestrator/lab/<lab-id>
```

The controller resolves it beneath `${XDG_DATA_HOME:-~/.local/share}` on the native Linux filesystem. Runtime state must never live in the repository, under `/mnt`, or inside an existing media/config directory.

JSON Schema validates the portable reference shape. The host controller must additionally prove at runtime that:

- the resolved root is a direct child of the trusted lab parent;
- every traversed path component is non-symlink and opened without following links;
- the final real path remains outside the checkout and `/mnt`;
- the root marker, manifest lab ID, Compose project suffix, and Docker resource labels all agree;
- directory modes are `0700`, and secret files are `0400` or `0600`.

## Network and authority boundary

The application network has:

- `internal: true`;
- `com.docker.network.bridge.gateway_mode_ipv4=isolated`;
- one application network per Compose project;
- zero published host ports.

No lab container receives the Docker socket. Docker/Compose authority remains on the trusted WSL host. Service API traffic and authenticated readiness run inside the isolated application network. Future implementation tasks must prove negative access to host listeners, LAN addresses, internet destinations, and the Docker socket while preserving required container-to-container communication.

## Secrets

Lab credentials are synthetic and generated per run. Contracts contain only references such as `file:/run/secrets/service-api-key` or desired-state references such as `file:sonarr/api-key`; they never contain secret values.

Secrets are mounted read-only from external runtime directories. They are not passed in Compose environment values, command-line arguments, healthcheck commands, logs, snapshots, fixtures, or evidence.

## Readiness

Readiness is not a single boolean:

1. `process_alive` — Docker-owned, non-secret process liveness;
2. `api_live` — Docker-owned, unauthenticated API liveness;
3. `api_ready` — controller-owned authenticated API and schema validation;
4. `baseline_verified` — controller-owned readback against the expected synthetic baseline digest.

Each state has a bounded interval, attempt count, and hard deadline. Fixed sleeps are not part of the contract. Runtime validation must prove that the hard deadline is coherent with the configured polling schedule and that polling uses a monotonic clock.

## Image lock

The image lock contains one entry for every required service/base image. Tags are informational; immutable content digests are authoritative. Floating `latest` tags, missing platform data, incomplete digests, undocumented provenance, or silent attestation gaps are rejected.

A verified signature or attestation requires an HTTPS evidence URL. An unavailable attestation requires an explicit limitation and is never presented as implicit success. Multiple runtime roles may resolve to one locked base image—for example, the lab controller and `arrctl` runner both use `controller-base`—and cross-contract tests require every readiness and security-matrix reference to resolve to a lock entry. Image updates are separate reviewed changes and must rerun adapter and synthetic end-to-end gates.

## Container security matrix

Every container drops all capabilities and enables `no_new_privileges`. A non-root long-running UID is mandatory. Upstream init may start as root, add a narrowly enumerated capability, or require a writable root filesystem only when the pinned-image limitation has an explicit rationale.

The matrix cannot grant Docker-socket access. Writable targets and tmpfs targets are enumerated; runtime inspection must prove the actual mounts, effective UID, capabilities, seccomp mode, PID limit, memory limit, and CPU limit match the matrix.

## Reset

Reset is a security protocol, not a convenience command. It requires:

- an exact trusted-parent/direct-child relationship;
- a valid lab marker;
- matching lab ID, Compose project, and resource labels;
- no-follow traversal and descriptor-relative deletion;
- bounded removal of only the identified project resources.

Global cleanup commands such as `docker system prune` are forbidden. Ambiguous ownership or containment moves the lab to quarantine instead of deletion.

## Public repository boundary

Generated application databases, service configuration, logs, runtime evidence, secret files, markers, private endpoints, and host paths are forbidden in Git. `scripts/check_public_safety.py` enforces that boundary for tracked and unignored repository files. The canonical public desired-state example is JSON and uses synthetic file references only.
