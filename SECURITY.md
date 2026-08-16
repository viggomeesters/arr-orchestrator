# Security policy

## Supported versions

Security fixes are applied to the latest release and the `main` branch.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities, credentials, private deployment details, or destructive-operation bypasses. Use GitHub's private vulnerability reporting for this repository. If that channel is unavailable, contact the maintainer through the private contact method listed on the maintainer's GitHub profile.

Include the affected version, impact, reproduction using synthetic data, and a proposed mitigation when available. Remove tokens, hostnames, IP addresses, media history, and command output from real deployments.

## Trust boundaries

Arr Orchestrator may eventually control privileged service APIs, storage paths, and remote hosts. Implementations must:

- separate read-only discovery from mutation;
- present a deterministic plan before apply;
- scope credentials to the minimum service and operation;
- avoid unrestricted Docker socket or root access;
- retain bounded, redacted evidence;
- require explicit authority for deletion, broad cleanup, credential rotation, or public exposure;
- verify the user-visible outcome after mutation.

A successful network request is not sufficient security or completion evidence.
