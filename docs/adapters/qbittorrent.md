# qBittorrent read-only adapter

The qBittorrent adapter uses the shared transport security authority with a file-backed custom API key. `QbittorrentReadOnlyTransport` sends the opaque key only as a Bearer credential to the configured origin and permits only same-origin `GET` requests. It creates no login session and performs no torrent, category, filesystem, or preference mutation.

## Discovery and reads

The adapter performs four authenticated reads:

- `/api/v2/app/version`;
- `/api/v2/app/webapiVersion`;
- `/api/v2/torrents/categories`;
- `/api/v2/sync/maindata?rid=0` for an explicit full snapshot.

Application and WebAPI versions become typed capabilities. Categories expose only category name and configured save path for doctor/path-mapping checks. Queue output contains only total count plus state and category counts.

Torrent hashes, names, item paths, magnet links, trackers, peer data, history, transfer identifiers, and raw response objects never leave the adapter. Lists are bounded to 10,000 items. Malformed versions, relative category paths, inconsistent category identities, malformed states, and oversized responses fail closed.

qBittorrent serializes `maindata` sparsely and omits an empty `torrents` map. The adapter accepts that omission only for an explicit full-update envelope with a non-negative integer response ID and a server-state map; a bare or incremental response without `torrents` fails closed.

## Transport boundary

`QbittorrentReadOnlyTransport` inherits the shared origin validation, proxy refusal, redirect refusal, TLS policy, one-deadline bounded retries, response-size limits, typed failures, sensitive-response detection, and redacted exceptions. The API key and transport representations are redacted. Non-GET requests fail before network I/O with `MUTATION_DISABLED`.

## Verification

```bash
python3 -m unittest tests.adapters.test_qbittorrent tests.transport.test_transport
python3 scripts/lab.py test adapter qbittorrent
```

The live lane starts digest-pinned qBittorrent `5.2.3`, mounts one generated API-key file read-only into an ephemeral non-root controller, proves exactly four GETs and zero authentication or mutation requests, publishes no host ports, checks output for credential leakage, and performs marker/project-bounded cleanup.
