# Security model

## Protected assets

- service API keys and session credentials;
- SSH keys and remote host access;
- media library and request history;
- filesystem topology and private network identifiers;
- generated mutation plans and operational evidence.

## Adversaries and failure sources

- accidental secret commits;
- over-broad agent authority;
- prompt or input content that attempts to expand scope;
- stale plans applied to changed infrastructure;
- API response drift and unsupported versions;
- path confusion between host and containers;
- destructive cleanup presented as routine maintenance;
- logs or fixtures that leak private deployment data.

## Controls

- read-only discovery by default;
- credential references rather than credential values;
- operation allowlists and explicit destructive-action gates;
- stable plan hashes and stale-plan rejection;
- redaction before persistence or Telegram output;
- synthetic fixtures in the public repository;
- external runtime directories with restrictive permissions;
- API readback and end-to-end verification after changes;
- repository and history secret scanning before publication.

Generated workflow deliveries are tracked only when their manifest declares
`disclosure.class: public` and `disclosure.scan_status: passed`. Restricted,
link-private, or scan-blocked delivery HTML belongs outside the public tree;
the canonical task and JSONL evidence remain sufficient for repository audit.

## Out of scope at foundation release

The repository does not yet connect to a live stack or perform remote mutations. Those capabilities require their own claimed Go tasks, tests, security review, and evidence.

## Disposable local lab boundary

The synthetic lab is the mandatory promotion environment for service integrations and mutation logic. It uses real service containers with repository-owned dependency doubles, but no live credentials, private media, private endpoint identifiers, or production configuration.

The application network is project-scoped, internal, isolated at the bridge gateway, and publishes no host ports. Containers receive no Docker socket. Docker/Compose authority remains on the WSL host; authenticated service probes execute inside the isolated network and read credentials from runtime-mounted files.

Runtime state resolves beneath `${XDG_DATA_HOME:-~/.local/share}/arr-orchestrator/lab/<lab-id>` on the native Linux filesystem. The controller must reject checkout paths, `/mnt` paths, symlink traversal, ownership mismatches, and ambiguous reset targets. Reset is limited to marker- and label-matched resources; global Docker cleanup is forbidden.

The four readiness states remain distinct: process liveness, unauthenticated API liveness, authenticated API readiness, and verified synthetic baseline. A running container is not proof of a healthy lab.
