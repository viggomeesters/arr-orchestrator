# Agent operating contract

Arr Orchestrator uses the JSON-first, repo-local `.go/` workflow. Repository files are the source of truth; chat history is not.

## Start here

1. Read `.go/project.json`.
2. Read `.go/vision.json`.
3. Read `.go/architecture-principles.json`.
4. Read `.go/hierarchy.json`.
5. Run `bash scripts/check.sh`.
6. Run `./go status . --json` and `./go next .`.
7. Claim exactly one task before editing product files.

`Go` is the single public execution command. Route it with:

```bash
./go router . --command go --intent "$PROMPT_TEXT" --json
```

Continue through the selected internal route without asking for another command unless a real authority, privacy, destructive-action, credential, or product-choice gate blocks execution.

## Scope discipline

- Modify only paths allowed by the claimed task.
- Preserve unrelated or user-authored changes.
- Never use destructive resets to manufacture a clean tree.
- First green is provisional: run the task checks, a critic/recheck pass, repair blocking findings, and rerun the gates.
- Finish tasks only with evidence and terminal outcomes for every acceptance requirement.

## Public-safety boundary

Never commit:

- API keys, passwords, tokens, cookies, private keys, or `.env` files;
- real hostnames, IP addresses, SSH configuration, VPN identifiers, or device inventories;
- real media titles, library exports, queue/download history, indexer records, or request history;
- generated runtime plans, logs, databases, caches, backups, or remote command output;
- screenshots or fixtures captured from a private deployment.

Use synthetic fixtures. Runtime state must live outside the checkout. Remote mutation must be plan-first, bounded, reversible where possible, and followed by readback verification.

## Product boundaries

Prefer official service APIs and controlled SSH operations. Browser automation is a last resort. Do not reimplement the media applications. Do not add a human dashboard before the doctor/plan/apply/verify control plane is proven.

## Required finish evidence

- task-specific verification commands;
- `bash scripts/check.sh`;
- `git diff --check`;
- privacy/secrets check;
- changed-path readback;
- clean or explicitly classified working tree;
- commit/push proof when shipping is authorized.

## GitHub Actions boundary

This repository uses documented local gates. Do not create, enable, invoke, or treat GitHub Actions as evidence unless the maintainer explicitly changes this policy.
