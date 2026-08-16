# Onboarding

This guide gets a developer or agent from a fresh clone to validated, claimable work without access to a private media stack.

## 1. Clone and validate

```bash
git clone https://github.com/viggomeesters/arr-orchestrator.git
cd arr-orchestrator
bash scripts/check.sh
```

The first run downloads the exact pinned `go-workflow-stack` release into the user's cache. Set `GO_STACK` only when developing against an explicit local stack checkout; add `GO_STACK_ALLOW_DEV=1` for an intentionally unpinned development checkout.

## 2. Understand the contracts

Read in this order:

1. `.go/vision.json` — product north star, audience, promise, wedge, non-goals, and success metrics.
2. `.go/architecture-principles.json` — durable technical constraints.
3. `docs/vision.json` — tested design, engineering, safety, and acceptance contract.
4. `docs/architecture.md` — runtime boundaries and trust model.
5. `.go/hierarchy.json` — epics, features, and task links.
6. `.go/tasks/open/*.json` — dependency-ordered executable work.

## 3. Find the next task

```bash
./go status . --json
./go next .
```

The next task is the first open task whose dependencies and repository gates permit work. Do not skip ahead merely because a later adapter appears easier.

## 4. Claim before editing

```bash
./go claim <task-id> --repo . --agent <agent-name>
```

Read the task's `scope.read`, `scope.modify`, acceptance requirements, and verification commands. Preserve unrelated changes.

## 5. Build and prove

Run the task-specific checks and then:

```bash
bash scripts/check.sh
git diff --check
```

Use synthetic fixtures. Do not connect tests to a real household service, commit captured responses, or write runtime state inside the checkout.

## 6. Review and finish

First green is provisional. Run a critic/recheck pass, repair blocking findings, rerun verification, record each required outcome with evidence, and finish through the Go CLI.

## Roles

- **Builder:** follows task scope and produces tested behavior.
- **Reviewer:** challenges side effects, redaction, fail-closed behavior, and evidence quality.
- **Operator:** supplies deployment authority and credentials outside Git.
- **Agent:** converts Telegram intent into deterministic operations and reports only verified state.

## Common mistakes

- implementing service logic in the conversational layer;
- treating a dashboard as the control plane;
- storing live API responses as fixtures;
- assuming paths are equivalent across containers;
- applying changes without a stable plan hash;
- calling an API success response an end-to-end success.
