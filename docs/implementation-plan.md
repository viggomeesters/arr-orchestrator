# Implementation plan

The product backlog is dependency-ordered in `.go/tasks/open/`. Each task is small enough for one agent to claim, implement, verify, review, and finish independently.

## Phase 1 — Contract and CLI skeleton

1. **`01-define-core-contracts`** — define desired-state, inventory, plan, evidence, capability, and error schemas.
2. **`02-build-arrctl-skeleton`** — create the installable CLI with JSON output, configuration discovery, and external runtime directories.

## Phase 2 — Read-only proof

3. **`03-implement-sonarr-readonly-adapter`** — version discovery, system status, root folders, download clients, and quality profiles against synthetic fixtures.
4. **`04-implement-radarr-readonly-adapter`** — the equivalent Radarr capability slice.
5. **`05-implement-prowlarr-readonly-adapter`** — application sync and indexer capability inventory.
6. **`08-implement-stack-inventory`** — combine adapter outputs into one normalized inventory.
7. **`09-implement-doctor-rules`** — diagnose connectivity, application wiring, path mappings, categories, profile gaps, and hardlink feasibility.

## Phase 3 — Safe mutation

8. **`10-implement-plan-engine`** — generate a deterministic, hashed, human-readable and machine-readable mutation plan.
9. **`11-implement-bounded-apply`** — apply allowlisted mutations with stale-plan rejection, backups where supported, and readback.
10. **`12-implement-end-to-end-verify`** — prove cross-service configuration and report explicit residual gaps.

## Phase 4 — Additional adapters and operator surface

11. **`06-implement-download-client-adapter`** — begin with qBittorrent capability and category/path checks.
12. **`07-implement-media-server-adapter`** — begin with Jellyfin health and library visibility checks.
13. **`13-implement-hermes-command-contract`** — map Telegram outcomes to `arrctl` operations and concise evidence responses.
14. **`14-prove-synthetic-stack-e2e`** — execute doctor → plan → apply → verify against a fully synthetic service stack.

## Dependency policy

- Core schemas precede the CLI and adapters.
- Read-only inventory and doctor must be proven before mutation.
- Plan identity and policy checks precede apply.
- Deletion and cleanup operations are excluded until bounded apply and verification are mature.
- A human dashboard remains outside this plan.

Use `./go next .` for the canonical next task rather than selecting from this narrative list.
