#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
bash scripts/validate-go.sh
python3 scripts/validate_vision.py
python3 scripts/check_docs.py
python3 scripts/check_public_safety.py
python3 scripts/check_hero.py
python3 -m json.tool docs/vision.json >/dev/null
python3 -m json.tool schemas/repo-vision-contract.schema.json >/dev/null
git diff --check
printf 'repository checks: ok
'
