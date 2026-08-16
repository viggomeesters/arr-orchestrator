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

## Out of scope at foundation release

The repository does not yet connect to a live stack or perform remote mutations. Those capabilities require their own claimed Go tasks, tests, security review, and evidence.
