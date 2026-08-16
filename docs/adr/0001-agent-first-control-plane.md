# ADR 0001: Build an agent-first control plane over existing services

- **Status:** Accepted
- **Date:** 2026-08-16

## Context

Self-hosted media automation distributes one user outcome across several specialized applications. Replacing those mature applications would create a large maintenance burden while leaving the cross-service configuration problem unsolved.

## Decision

Build Arr Orchestrator as an agent-first control plane. Telegram is the human interface; `arrctl`, official service APIs, controlled SSH operations, policies, and evidence form the machine interface.

The first product slice is read-only inventory and diagnosis. Mutation follows only after deterministic planning, policy checks, and verification contracts are proven.

## Consequences

- Existing services retain domain ownership.
- Adapter boundaries and version discovery are first-class.
- Runtime state and credentials remain external to Git.
- A dashboard is not required for initial product value.
- Remote deletion and broad cleanup need explicit authority and dedicated safety work.
