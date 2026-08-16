# `arrctl` CLI

`arrctl` is the deterministic execution surface used by agents and operators.
The current skeleton is intentionally local-only: it exposes the stable command
surface without contacting or modifying any *arr service.

## Installation

Use an isolated environment. Do not install into the system Python:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/arrctl --help
```

The package also supports module execution:

```bash
PYTHONPATH=src python3 -m arr_orchestrator --help
```

## Commands

| Command | Current skeleton behavior |
| --- | --- |
| `doctor` | Returns `assessment: not_run` with an empty check set. |
| `plan` | Returns `plan_state: not_generated` with an empty operation map. |
| `apply` | Fails closed because mutation support is not implemented. |
| `verify` | Returns `assessment: not_run` with an empty check set. |
| `status` | Shows external runtime paths and `runtime_initialized: false`. |
| `version` | Shows the installed package version. |

Every command accepts `--json` before or after the command name:

```bash
arrctl --json status
arrctl status --json
```

JSON responses use the `arr-orchestrator.cli-result.v1` envelope and always
include the command, status, and numeric exit code. Skeleton responses also
include `remote_side_effects: false`.

Domain and configuration failures use the versioned core `error` contract.
CLI syntax and unexpected process failures instead use a small `cli_error`
object with `USAGE_INVALID` or `INTERNAL_ERROR`; they do not impersonate a
domain error such as `CONFIG_INVALID` or `APPLY_FAILED`. Both forms are
redacted and never echo rejected argument values or exception text.

## Exit codes

Exit codes are stable API, not prose conventions:

| Code | Name | Meaning |
| ---: | --- | --- |
| `0` | success | Command completed. |
| `2` | usage | Invalid command-line syntax or unknown command. |
| `3` | configuration | Configuration or runtime path is invalid. |
| `4` | policy | Required policy or human authority is missing. |
| `5` | operation | Requested operation cannot be executed. |
| `6` | verification | Verification failed. |
| `70` | internal | Unexpected failure, reported without private details. |

`apply` currently returns exit code `5` and a typed `CAPABILITY_MISSING` error.
It performs no remote request or mutation.

## Runtime directories

Runtime state never belongs in the repository checkout. Resolution order is:

1. `ARR_ORCHESTRATOR_CONFIG_DIR` or `ARR_ORCHESTRATOR_DATA_DIR`;
2. `XDG_CONFIG_HOME/arr-orchestrator` or
   `XDG_DATA_HOME/arr-orchestrator`;
3. `~/.config/arr-orchestrator` and
   `~/.local/share/arr-orchestrator`.

Paths must be absolute and outside Git repository checkouts. Final paths are
canonicalized before containment checks, including existing symlinks. Unsafe
paths fail closed with a redacted `CONFIG_INVALID` response. Higher-priority
explicit and XDG paths do not depend on a valid lower-priority `HOME` value.
Directory discovery does not create the directories; later configuration and
persistence tasks own that side effect.
