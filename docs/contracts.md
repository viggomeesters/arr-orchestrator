# Core contracts

Arr Orchestrator uses strict, versioned JSON contracts between agents, the `arrctl` control plane, service adapters, policy logic, and evidence consumers. These contracts exist before CLI or adapter implementation so later components share one vocabulary and fail closed on unknown data.

## Contract set

Version 1 lives in [`schemas/contracts/v1/`](../schemas/contracts/v1/).

| Contract | Artifact kind | Purpose |
| --- | --- | --- |
| `desired-state.schema.json` | `desired_state` | Declarative service intent, media-root references, secret references, and operator policies |
| `capabilities.schema.json` | `capabilities` | Reachability, API version, health, supported operations, and known limitations |
| `inventory.schema.json` | `inventory` | Normalized service resources, storage identity, and cross-service connections |
| `findings.schema.json` | `findings` | Doctor rule results, ownership, evidence references, and pass/warn/block totals |
| `plan.schema.json` | `plan` | Digest-bound mutations with exact targets, risk, approval requirements, and assumptions |
| `evidence.schema.json` | `evidence` | Redacted readback or end-to-end assertions with content identity |
| `error.schema.json` | `error` | Typed, redacted, owner-attributed failures and blockers |

Every contract uses JSON Schema Draft 2020-12, a unique versioned URN, `contract_version: "1.0.0"`, a fixed `artifact_kind`, and closed object shapes.

## Compatibility policy

- Additive optional fields require a minor contract release and must not change existing meaning.
- New required fields, removed fields, changed enums, or changed semantics require a new major contract directory.
- Producers emit exactly one declared version.
- Consumers reject unknown major versions and unknown fields rather than guessing.
- Migration belongs in explicit converters; schemas are never silently reinterpreted.

## Identity and references

Contracts use stable logical references instead of deployment-identifying values:

- `service_id`: stable operator-defined identity such as `sonarr-main`;
- `resource_key`: exact service-local or desired-state identity;
- `path_ref`: logical mount reference such as `mount:media/series`;
- `secret_ref`: pointer such as `env:SONARR_API_KEY` or `file:sonarr/api-key`;
- `sha256:<digest>`: immutable content identity for plans, inventories, desired state, and evidence.

A reference is not permission. Runtime resolution, credential access, and remote authority remain separate concerns.

Identity-bearing collections are JSON objects keyed by their canonical identity,
not arrays containing an identity field. Service, resource, storage, finding, and
operation identities therefore cannot appear twice with contradictory values.
JSON decoders used by the control plane must also reject duplicate member names
instead of silently retaining the last value.

## Privacy boundary

The public contract vocabulary deliberately excludes fields such as:

- API-key or password values;
- hostnames, IP addresses, SSH targets, and VPN identifiers;
- raw filesystem paths;
- media titles, queue history, download history, and indexer results;
- raw API responses or command output.

Strict `additionalProperties: false` declarations reject undeclared fields at every object boundary. Capability limitations use typed codes instead of free text. Runtime artifacts may instantiate these contracts outside Git, but persisted evidence must remain redacted and use logical references.

The schema can close fields and constrain references; it cannot prove that an
otherwise valid human-readable `message` or `summary` contains no private text.
Producers must redact before validation, and anything persisted or published must
also pass the repository public-safety scanner. The `redacted` flag is an audited
producer assertion, not a substitute for content scanning.

## Plan invariants

A mutation operation is executable only when it has:

1. an immutable plan identity;
2. the inventory and desired-state digests it was derived from;
3. one canonical operation reference in the form
   `service_id:resource_type:resource_key:field`;
4. one bounded change stored under that unique reference;
5. an explicit risk class;
6. explicit approval state.

Fuzzy selectors and targetless operations are invalid. Operations that require
human authority carry an approval record; risky and destructive operations always
require one, while safe operations may also require one under the desired-state
`plan_approval: always` policy. Approved records require authority, evidence, and
decision-time references. Delete operations are always `destructive`. A plan with
unresolved assumptions is structurally `blocked`, and a `ready` or `applied` plan
cannot contain pending approval records.

Verification evidence links back to a mutation through the same canonical
`operation_ref`; it never invents a second operation identifier.

JSON Schema verifies digest shape but cannot recompute a digest. The plan engine
must canonicalize the plan, recompute `plan_id`, and compare inventory and desired-
state digests immediately before apply. It must also reject unsupported capabilities.

Credential-bearing mutation fields such as `api_key`, `password`, `token`,
`secret`, or `private_key` are invalid. Credential rotation is represented by
a secret reference at the configuration boundary, never by embedding the
credential value in a plan.

## Error taxonomy

| Code | Typical owner | Meaning |
| --- | --- | --- |
| `CONFIG_INVALID` | configuration loader | Desired state or local configuration is invalid |
| `AUTH_MISSING` | service adapter | A required secret reference cannot be resolved |
| `SERVICE_UNREACHABLE` | service adapter | The service cannot be contacted safely |
| `VERSION_UNSUPPORTED` | service adapter | The discovered API or service version is unsupported |
| `CAPABILITY_MISSING` | adapter or policy | The requested operation is not supported |
| `PATH_AMBIGUOUS` | inventory or doctor | Path identity cannot be proven across services |
| `AUTHORITY_REQUIRED` | policy engine | The operation requires stronger operator authority |
| `PLAN_STALE` | plan engine | Current inventory or intent differs from the approved plan |
| `BACKUP_FAILED` | apply engine | Required rollback material could not be created |
| `APPLY_FAILED` | apply engine | A bounded mutation failed |
| `VERIFICATION_FAILED` | verifier | Readback cannot prove the requested outcome |
| `PRIVATE_DATA_REDACTED` | evidence layer | Unsafe content was withheld from persistence or output |

Errors carry category, severity, retryability, owning component, closed redacted details, and a stable error identity. Messages explain the condition without echoing credentials or private values.
Each code is schema-bound to its canonical category so producers cannot relabel
an authentication failure as a policy or connectivity result.

## Synthetic fixture verification

Positive and negative fixtures live under [`tests/contracts/fixtures/`](../tests/contracts/fixtures/). They contain synthetic identities only.

Run:

```bash
python3 -m unittest discover -s tests/contracts -p "test_*.py"
```

The suite proves:

- all seven schemas are valid Draft 2020-12 schemas;
- positive fixtures validate without network access;
- undeclared private fields are rejected;
- ambiguous mutation targets are rejected;
- identity-bearing collections are key-addressed rather than duplicate-prone arrays;
- destructive operations without approval are rejected;
- ready plans reject unresolved assumptions and pending approvals;
- credential fields and traversing secret references are rejected;
- capability limitations are typed codes rather than private free text;
- error codes cannot drift into a different category;
- unredacted evidence is rejected;
- duplicate JSON member names and non-RFC3339 timestamps are rejected;
- every object boundary is closed;
- schema identities and envelopes remain versioned and strict.
