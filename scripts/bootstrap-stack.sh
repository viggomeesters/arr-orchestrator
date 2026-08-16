#!/usr/bin/env bash
set -euo pipefail

# Ensure a fresh go-project-template clone can run `bash scripts/check.sh`
# without the user manually cloning the stack first.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${GO_STACK_REF:-}" ]; then
  echo "GO_STACK_REF cannot override .go/project.json; update the repo-local stack_ref contract instead" >&2
  exit 5
fi
STACK_REF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stack_ref"])' "$REPO_ROOT/.go/project.json")"
DEFAULT_STACK="${XDG_CACHE_HOME:-${HOME:?HOME is required}/.cache}/go-workflow-stack/$STACK_REF"
EXPLICIT_STACK=0
if [ -n "${GO_STACK:-}" ]; then
  EXPLICIT_STACK=1
else
  GO_STACK="$DEFAULT_STACK"
fi
STACK_REMOTE="${GO_STACK_REMOTE:-https://github.com/viggomeesters/go-workflow-stack.git}"

runtime_matches() {
  local checkout="$1" expected_commit head
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$STACK_REF" =~ ^[0-9a-f]{40}$ ]]; then
    expected_commit="$STACK_REF"
  else
    expected_commit="$(git -C "$checkout" rev-parse -q --verify "refs/tags/$STACK_REF^{commit}" 2>/dev/null || true)"
  fi
  [ -n "$expected_commit" ] && [ "$head" = "$expected_commit" ]
}

allow_development_checkout() {
  [ "$EXPLICIT_STACK" = "1" ] && [ "${GO_STACK_ALLOW_DEV:-0}" = "1" ]
}

managed_origin_matches() {
  [ "$(git -C "$GO_STACK" remote get-url origin 2>/dev/null || true)" = "$STACK_REMOTE" ]
}

if [ ! -d "$GO_STACK/.git" ]; then
  mkdir -p "$(dirname "$GO_STACK")"
  if [[ "$STACK_REF" =~ ^[0-9a-f]{40}$ ]]; then
    git clone --no-checkout "$STACK_REMOTE" "$GO_STACK"
    git -C "$GO_STACK" checkout --detach --quiet "$STACK_REF"
  else
    git clone --branch "$STACK_REF" --depth 1 "$STACK_REMOTE" "$GO_STACK"
  fi
elif [ "$EXPLICIT_STACK" = "1" ]; then
  if ! runtime_matches "$GO_STACK"; then
    if allow_development_checkout; then
      echo "warning: GO_STACK_ALLOW_DEV=1 development override accepts unpinned explicit GO_STACK; expected $STACK_REF" >&2
    else
      echo "explicit GO_STACK does not provide pinned runtime $STACK_REF (exact commit required)" >&2
      exit 4
    fi
  fi
else
  if ! managed_origin_matches; then
    echo "managed go-workflow-stack cache origin does not match GO_STACK_REMOTE: $GO_STACK" >&2
    exit 6
  fi
  if [ -n "$(git -C "$GO_STACK" status --porcelain)" ]; then
    echo "go-workflow-stack checkout is dirty; commit, stash, or clean it before bootstrap updates" >&2
    exit 3
  fi
  if ! runtime_matches "$GO_STACK"; then
    git -C "$GO_STACK" fetch --quiet --depth 1 origin "$STACK_REF"
    git -C "$GO_STACK" checkout --detach --quiet FETCH_HEAD
  fi
fi

if [ ! -f "$GO_STACK/cli/go.py" ]; then
  echo "go-workflow-stack CLI not found at $GO_STACK/cli/go.py" >&2
  exit 1
fi

if ! runtime_matches "$GO_STACK"; then
  if allow_development_checkout; then
    echo "warning: development override remains active for unpinned runtime $GO_STACK" >&2
  else
    echo "go-workflow-stack runtime commit mismatch: expected exact ref $STACK_REF" >&2
    exit 4
  fi
fi

printf '%s\n' "$GO_STACK"
