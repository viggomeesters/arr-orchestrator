# Repository foundation

## Current release

- Repository: `viggomeesters/arr-orchestrator`
- Foundation version: `0.1.0`
- Default branch: `main`
- License: MIT
- Product implementation: represented by dependency-ordered repo-local Go tasks

## Public surfaces

- README hero and product explanation;
- machine-readable product and design contracts;
- architecture, onboarding, implementation, security, contribution, support, and release documentation;
- local validation gates with no private infrastructure dependency;
- synthetic desired-state example;
- public-safety and generated-runtime-state guards.

## Validation contract

```bash
bash scripts/check.sh
python3 scripts/validate_vision.py
python3 scripts/check_public_safety.py
python3 scripts/check_hero.py
```

Repository publication additionally requires repo-complete public validation, GitHub metadata readback, a first release, history review, and a fresh-clone check.
