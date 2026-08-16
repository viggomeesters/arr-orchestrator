# Arr Orchestrator v0.1.0

The first release establishes the public, agent-first foundation for a Telegram-operated media automation control plane.

## Included

- repo-local `.go` product vision, architecture principles, hierarchy, decisions, evidence, and dependency-ordered tasks;
- tested `docs/vision.json` design and engineering contract;
- architecture, onboarding, implementation, security, contribution, support, and release documentation;
- deterministic local gates for workflow, schema, documentation, hero, and public-safety validation;
- synthetic desired-state example with secrets and runtime state kept outside Git;
- rendered README hero and GitHub social-preview asset.

## Product boundary

This release does not connect to live Sonarr, Radarr, Prowlarr, download-client, or media-server instances. Product implementation starts with the first open Go task and must prove read-only contracts before mutation.

## Validation

```bash
bash scripts/check.sh
./go validate .
./go next .
```