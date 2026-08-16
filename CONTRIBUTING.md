# Contributing

Arr Orchestrator is built through small, evidence-backed Go tasks. Contributions should improve a complete operational slice rather than add disconnected surface area.

## Development setup

```bash
git clone https://github.com/viggomeesters/arr-orchestrator.git
cd arr-orchestrator
bash scripts/check.sh
./go status . --json
```

## Before changing files

1. Read `AGENTS.md` and `docs/vision.json`.
2. Select or create one `.go` task with explicit scope, acceptance criteria, and verification.
3. Claim the task before editing.
4. Use only synthetic fixtures. Never copy data from a live media stack.

## Change expectations

- Prefer stable APIs and machine-readable JSON output.
- Keep remote changes plan-first and fail closed on missing evidence.
- Add focused tests for every behavior change.
- Document new configuration, security boundaries, and failure modes.
- Avoid drive-by formatting or unrelated cleanup.

## Local checks

```bash
make check
git diff --check
```

## Commit style

Use focused, imperative commits, for example:

```text
feat: add read-only Sonarr capability discovery
fix: reject ambiguous remote path mappings
docs: explain adapter trust boundaries
```

## Pull requests

Describe the user outcome, changed boundaries, commands executed, evidence produced, and residual risk. A passing command without relevant behavioral evidence is insufficient.
