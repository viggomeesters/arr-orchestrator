# Jellyfin read-only adapter

The Jellyfin adapter targets the official 10.11 server API and delegates all HTTP security policy to the shared transport. Runtime reads use a protected, file-backed access token in the allowlisted `X-Emby-Token` header. The synthetic lab provisions a fresh server with four explicitly classified startup mutations, obtains that ephemeral token through exactly one separately classified authentication request, and then runs the normal adapter with GET-only operation. Setup traffic is never reported as adapter traffic.

## Read-only resources

- `GET /System/Info/Public` — server version and startup-complete readiness. Detailed authenticated shutdown/restart flags remain explicitly unsupported because that response contains private server/network identity fields.
- `GET /Library/VirtualFolders` — collection type, absolute storage locations, and whether a library refresh is currently reporting progress.
- `GET /ScheduledTasks` — only the `RefreshLibrary` task's bounded state and progress.

Public models are immutable. They exclude server names and IDs, usernames, media and library display names, item/task/image IDs, descriptions, execution errors, titles, provider payloads, headers, tokens, and raw response objects. Library locations are intentionally retained because path visibility and later path-mapping verification are part of the orchestration contract.

Collections are bounded to 10,000 entries. Versions, booleans, absolute paths, collection types, task states, progress values, and duplicate refresh tasks are validated fail-closed. A missing `RefreshLibrary` task is represented explicitly as unsupported rather than guessed.

Authentication, missing resources, unreachable services, TLS/deadline failures, malformed responses, and unsupported versions remain typed and context-free. The adapter exposes no mutating method and imports no HTTP implementation.
