import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.adapters.prowlarr import ProwlarrAdapter, ProwlarrAdapterFailure
from arr_orchestrator.transport import TransportFailure

FIXTURES = ROOT / "tests" / "fixtures" / "prowlarr"


class FakeTransport:
    def __init__(self, responses=None, failure=None):
        self.responses = {key: list(value) for key, value in (responses or {}).items()}
        self.failure = failure
        self.calls = []

    def get_json(self, path, *, query=None):
        self.calls.append((path, query))
        if self.failure is not None:
            raise self.failure
        if path not in self.responses or not self.responses[path]:
            raise AssertionError(f"unexpected request: {path}")
        return self.responses[path].pop(0)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ProwlarrAdapterTests(unittest.TestCase):
    def responses(self):
        return {
            "/api/v1/system/status": [fixture("system-status.json")],
            "/api/v1/applications": [fixture("applications.json")],
            "/api/v1/indexer": [fixture("indexers.json")],
        }

    def test_discovers_v1_and_normalizes_private_free_snapshot(self):
        transport = FakeTransport(self.responses())
        snapshot = ProwlarrAdapter(transport).read_snapshot()
        payload = snapshot.to_dict()

        self.assertEqual(1, payload["capabilities"]["api_version"])
        self.assertEqual("2.5.2.5491", payload["capabilities"]["application_version"])
        self.assertEqual("Prowlarr", payload["system_status"]["application"])
        self.assertEqual(
            [
                {"id": 1, "name": "Synthetic Sonarr", "implementation": "Sonarr", "sync_level": "fullSync"},
                {"id": 2, "name": "Synthetic Radarr", "implementation": "Radarr", "sync_level": "addOnly"},
            ],
            payload["applications"],
        )
        self.assertEqual(
            {
                "total": 2,
                "enabled": 1,
                "rss_capable": 2,
                "search_capable": 1,
                "protocol_counts": {"torrent": 1, "usenet": 1},
                "privacy_counts": {"private": 1, "public": 1},
            },
            payload["indexers"],
        )
        encoded = repr(snapshot) + json.dumps(payload, sort_keys=True)
        for private in (
            "private-application-key-canary",
            "private-indexer-key-canary",
            "private-query-canary",
            "private-sonarr.invalid",
            "private-indexer.invalid",
            "fields",
            "indexerUrls",
        ):
            self.assertNotIn(private, encoded)
        self.assertEqual(
            [
                ("/api/v1/system/status", None),
                ("/api/v1/applications", None),
                ("/api/v1/indexer", None),
            ],
            transport.calls,
        )

    def test_discovery_404_maps_only_to_unsupported_api(self):
        with self.assertRaises(ProwlarrAdapterFailure) as caught:
            ProwlarrAdapter(FakeTransport(failure=TransportFailure("RESOURCE_NOT_FOUND"))).discover_capabilities()
        self.assertEqual("UNSUPPORTED_API_VERSION", caught.exception.code)

        for code in ("AUTH_FAILED", "SERVICE_UNREACHABLE", "TLS_VERIFICATION_FAILED"):
            with self.subTest(code=code), self.assertRaises(ProwlarrAdapterFailure) as failure:
                ProwlarrAdapter(FakeTransport(failure=TransportFailure(code, retryable=True))).discover_capabilities()
            self.assertEqual(code, failure.exception.code)
            self.assertTrue(failure.exception.retryable)

    def test_later_404_remains_resource_not_found(self):
        responses = self.responses()
        transport = FakeTransport(responses)
        transport.failure = None
        original = transport.get_json

        def fail_later(path, *, query=None):
            if path == "/api/v1/applications":
                raise TransportFailure("RESOURCE_NOT_FOUND")
            return original(path, query=query)

        transport.get_json = fail_later
        with self.assertRaises(ProwlarrAdapterFailure) as caught:
            ProwlarrAdapter(transport).read_snapshot()
        self.assertEqual("RESOURCE_NOT_FOUND", caught.exception.code)

    def test_rejects_wrong_identity_malformed_values_and_oversized_lists(self):
        cases = []
        wrong = self.responses()
        wrong["/api/v1/system/status"] = [{"appName": "Sonarr", "version": "4.0.0", "branch": "main"}]
        cases.append((wrong, "SERVICE_IDENTITY_INVALID"))
        bad_sync = self.responses()
        bad_sync["/api/v1/applications"] = [[{"id": 1, "name": "x", "implementation": "Sonarr", "syncLevel": "everything"}]]
        cases.append((bad_sync, "RESPONSE_SHAPE_INVALID"))
        bad_indexer = self.responses()
        bad_indexer["/api/v1/indexer"] = [[{"enable": True, "protocol": "torrent", "privacy": "public", "supportsRss": "yes", "supportsSearch": True}]]
        cases.append((bad_indexer, "RESPONSE_SHAPE_INVALID"))
        too_many = self.responses()
        too_many["/api/v1/indexer"] = [[{}] * 10001]
        cases.append((too_many, "RESPONSE_BOUNDS_INVALID"))
        for responses, code in cases:
            with self.subTest(code=code), self.assertRaises(ProwlarrAdapterFailure) as caught:
                ProwlarrAdapter(FakeTransport(responses)).read_snapshot()
            self.assertEqual(code, caught.exception.code)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

    def test_transport_failure_is_contextless_and_private_free(self):
        private = "private-prowlarr-credential-canary"
        try:
            ProwlarrAdapter(FakeTransport(failure=TransportFailure("AUTH_FAILED"))).read_snapshot()
        except ProwlarrAdapterFailure as failure:
            self.assertEqual("AUTH_FAILED", failure.code)
            self.assertNotIn(private, repr(failure))
            self.assertIsNone(failure.__cause__)
            self.assertIsNone(failure.__context__)
        else:
            self.fail("expected ProwlarrAdapterFailure")


if __name__ == "__main__":
    unittest.main()
