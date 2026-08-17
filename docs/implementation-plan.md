# Implementation plan

The product backlog is dependency-ordered in `.go/`. Every task is independently claimable, has an explicit modify scope, and must pass focused verification, repository gates, independent review, finish evidence, commit, push, and remote readback before its dependants become eligible.

## Phase 1 — Contracts and CLI foundation

1. **`01-define-core-contracts`** — versioned desired-state, inventory, plan, evidence, capability, and error schemas. *(done)*
2. **`02-build-arrctl-skeleton`** — installable CLI, deterministic JSON envelope, typed exit codes, and external runtime directories. *(done)*

## Phase 2 — Disposable local Docker lab

3. **`lab-00-register-lab-workstream`** — register the lab hierarchy, task contracts, decisions, priorities, and dependencies. No product implementation.
4. **`lab-01-define-local-stack-contract`** — define strict lab manifest, image lock, readiness, security-matrix, network, secret, reset, and public-safety contracts.
5. **`lab-02-build-compose-foundation`** — build the digest-locked Compose topology and controller/runner image; prove isolation without starting unseeded real services.
6. **`lab-03-add-deterministic-dependency-doubles`** — add a synthetic indexer and deterministic protocol-fault API.
7. **`lab-04-bootstrap-services`** — safely start and seed real local Sonarr, Radarr, Prowlarr, qBittorrent, and Jellyfin through supported APIs, reaching `baseline_verified` without UI interaction.
8. **`lab-05-implement-scenario-controller`** — switch between the canonical healthy baseline and allowlisted broken states using one explicit authority per scenario.

The MVP lab publishes no host ports. Application services use one internal bridge with isolated gateway mode, receive no Docker socket, use only synthetic data and per-run credentials, and keep all generated state under a marked external XDG runtime root.

## Phase 3 — Shared read-only integration

9. **`transport-01-build-shared-readonly-transport`** — centralize runtime endpoints, credential references, URL/TLS policy, timeouts, retries, typed failures, method policy, and redaction.
10. **`03-implement-sonarr-readonly-adapter`** — fixture and real-container capability/configuration discovery.
11. **`04-implement-radarr-readonly-adapter`** — fixture and real-container capability/configuration discovery.
12. **`05-implement-prowlarr-readonly-adapter`** — application-link, sync, and synthetic-indexer discovery.
13. **`06-implement-download-client-adapter`** — qBittorrent authentication, capability, category, queue, and path discovery without persisting download names.
14. **`07-implement-media-server-adapter`** — Jellyfin health and empty synthetic-library visibility without persisting media titles.
15. **`08-implement-stack-inventory`** — combine all five adapters into one normalized inventory while preserving uncertainty and service-level evidence.
16. **`09-implement-doctor-rules`** — diagnose connectivity, API support, application wiring, path mappings, categories, root folders, and actual hardlink feasibility.
17. **`lab-06-prove-readonly-stack`** — independent promotion gate over the complete real local lab and deterministic fault scenarios.

Read-only mode denies domain mutation. The only non-GET exception is the explicitly classified, side-effect-free qBittorrent authentication-session request.

## Phase 4 — Planned and verified mutation

18. **`10-implement-plan-engine`** — produce byte-stable, hashed, bounded mutation plans from verified observed state.
19. **`11-implement-bounded-apply`** — apply only allowlisted non-destructive operations after policy and approval, rejecting stale plans.
20. **`12-implement-end-to-end-verify`** — rediscover state after apply and assign every requested outcome a terminal, evidenced status.
21. **`lab-07-prove-plan-apply-verify`** — prove doctor → plan → approval → apply → API readback → verify, stale-plan denial, destructive denial, and healthy reset in the disposable lab.

## Phase 5 — Hermes contract and release proof

22. **`13-implement-hermes-command-contract`** — map Telegram/Hermes intent to deterministic `arrctl` operations through `arrctl-runner`; keep Telegram credentials, Hermes runtime, and Docker authority outside Compose.
23. **`14-prove-synthetic-stack-e2e`** — final release gate over the real local service containers plus deterministic doubles:

```text
broken scenario
→ inventory
→ doctor
→ plan
→ approval
→ apply
→ API readback
→ verify
→ redacted evidence
→ guarded reset
→ healthy inventory
```

## Dependency policy

- Lab and shared transport precede every service adapter.
- Inventory depends on all five adapters; doctor depends on inventory.
- `lab-06` must pass before mutation planning begins.
- Plan identity and policy precede apply; fresh readback verification follows every mutation.
- `lab-07` must pass before the Hermes command contract becomes eligible.
- Task 14 is the final synthetic release gate, not a substitute for adapter, inventory, doctor, or mutation tests.
- No task may use private deployment data, manipulate application databases directly, expose application ports, or weaken approval and stale-plan protections because the target is disposable.
- The real deployment remains out of scope until the corresponding lab chain is green. Its first promotion step is explicit read-only access.
- A human dashboard remains outside this plan.

Use `./go next .` as the canonical task selector; this document explains the sequence but does not override repository-local task state.
