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

Explanations and remediation text are selected by trusted code. They are never copied from provider responses, exception messages, service names, paths, task descriptions, or arbitrary evidence.

## Required cross-service evidence

Doctor requires exactly identified checks for:

- the Arr download category to qBittorrent relationship;
- Prowlarr application links to Sonarr and Radarr;
- Sonarr and Radarr root-folder relationships;
- the shared container-path identity;
- downloads-to-media hardlink feasibility.

Checks publish only a normalized status and opaque references. Raw category names, endpoint URLs, root paths, mount sources, server identities, credentials, media names, and provider payloads remain behind the owning adapter or runtime boundary.

Missing, unavailable, or ambiguous required evidence is a blocker. Doctor does not silently choose one interpretation.

## Inventory rules

The doctor also evaluates normalized service evidence:

- unreachable, unknown, partial, and unsupported services remain explicit blockers;
- unsupported API versions produce `API_VERSION_UNSUPPORTED`;
- Sonarr and Radarr require accessible root folders, an enabled download client, and a quality profile;
- Prowlarr requires the expected application-link count in addition to exact link evidence;
- qBittorrent requires a category in addition to exact category evidence;
- Jellyfin must be healthy and startup-complete.

A stack-level `healthy` inventory only means all adapters completed their readback. Doctor may still block configuration that is reachable but incomplete or miswired.

## Hardlinks and path identity

Hardlink feasibility is proven by a bounded filesystem operation: create a synthetic source, attempt the link, read the outcome, and remove both files. Device numbers alone are supporting evidence, not the final claim.

Container-path identity is fail-closed. An absent or ambiguous mount relationship produces a blocker instead of an inferred mapping.

## Lab verification

Run:

```bash
python3 scripts/lab.py test doctor
```

The isolated lane uses digest-pinned services, an internal network, no published host ports, project-labelled resources, synthetic credentials, and bounded cleanup. It proves normalized findings for category mismatch, application-link mismatch, root-folder mismatch, container-path mismatch, service unavailability, unsupported API version, and actual cross-device hardlink failure. Private API details are evaluated only inside the credential-owning controller; the final report exposes codes and statuses only.
