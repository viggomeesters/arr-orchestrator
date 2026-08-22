# Doctor rules

The doctor converts normalized stack inventory plus a closed set of cross-service evidence checks into deterministic, actionable findings. It does not mutate services, infer missing state as healthy, or expose provider payloads.

## Public report

The canonical schema is `arr-orchestrator.doctor-report.v1`. A report contains:

- `state`: `healthy`, `degraded`, or `blocked`;
- a severity summary;
- deterministically ordered findings.

Every finding contains:

- a stable public code;
- severity;
- an accountable owner;
- one or more opaque evidence references;
- a static explanation;
- a static remediation step.

Explanations, remediation text, owners, severities, and evidence references are selected by trusted code. They are never copied from provider responses, exception messages, service names, paths, task descriptions, or arbitrary evidence. Public finding and report constructors revalidate this closed registry, and serialization repeats the validation so post-construction mutation cannot bypass it.

## Required cross-service evidence

Doctor requires exactly identified checks for:

- the Arr download category to qBittorrent relationship;
- Prowlarr application links to Sonarr and Radarr;
- Sonarr and Radarr root-folder relationships;
- the shared container-path identity;
- downloads-to-media hardlink feasibility.

Callers supply only the exact check identifier and normalized status. They cannot supply evidence references. Doctor maps each check identifier to a fixed opaque public reference. Raw category names, endpoint URLs, root paths, mount sources, server identities, credentials, media names, and provider payloads remain behind the owning adapter or runtime boundary.

Missing, unavailable, or ambiguous required evidence is a blocker. Doctor does not silently choose one interpretation.

## Inventory rules

The doctor also evaluates normalized service evidence:

- unreachable, unknown, partial, and unsupported services remain explicit blockers;
- unsupported API versions produce `API_VERSION_UNSUPPORTED`;
- Sonarr and Radarr require accessible root folders, an enabled download client, and a quality profile;
- Prowlarr requires the expected application-link count in addition to exact link evidence;
- qBittorrent requires a category in addition to exact category evidence;
- Jellyfin must be healthy and startup-complete.

The stack-level state must equal the deterministic reduction of the five exact service states. Duplicate evidence keys, negative counts, enabled counts above totals, and inaccessible root-folder counts above root-folder totals are rejected before diagnosis. Contradictory or malformed normalized evidence cannot become a healthy report.

Each service state has one closed payload shape. `available` rows have a normalized successful version, no unsupported resources, no failure code, and are not retryable. `partial` rows have a normalized successful version plus at least one unsupported resource that is disjoint from supported resources, with no failure metadata. `unknown`, `unreachable`, and `unsupported` rows contain no successful snapshot payload and require a failure code from the matching closed family. Stale resources, evidence, versions, or failure metadata are rejected before diagnosis.

A stack-level `healthy` inventory only means all adapters completed their readback. Doctor may still block configuration that is reachable but incomplete or miswired.

## Hardlinks and path identity

Hardlink feasibility is proven by a bounded filesystem operation: create a synthetic source, attempt the link, read the outcome, and remove both files. Device numbers alone are supporting evidence, not the final claim.

Container-path identity is fail-closed. An absent or ambiguous mount relationship produces a blocker instead of an inferred mapping.

## Lab verification

Run:

```bash
python3 scripts/lab.py test doctor
```

The isolated lane uses digest-pinned services, an internal network, no published host ports, project-labelled resources, synthetic credentials, and bounded cleanup. Its healthy cross-service statuses are reduced from exact controller readback of the qBittorrent category, both Arr root folders, both Prowlarr application links, and Sonarr path mappings; Docker inspection additionally proves that Sonarr and Radarr share the same `/data` host source while qBittorrent and Jellyfin expose the exact downloads and media subpaths. An actual hardlink operation completes the healthy storage proof. Fault findings are reduced from the mutated readback rather than from scenario names.

The unsupported-API scenario configures the private fault API, then runs the shared HTTP transport, the real Prowlarr adapter, the inventory builder, and the doctor rule against `/api/v1/system/status`. The lane requires an adapter-produced `UNSUPPORTED_API_VERSION` readback before accepting the public `API_VERSION_UNSUPPORTED` finding. Private API details are evaluated only inside credential-owning containers; the final report exposes codes and statuses only.
