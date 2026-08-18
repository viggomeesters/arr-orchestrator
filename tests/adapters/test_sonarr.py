import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.adapters.sonarr import SonarrAdapter, SonarrAdapterFailure
from arr_orchestrator.transport import TransportFailure


FIXTURES = ROOT / "tests" / "fixtures" / "sonarr"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    def __init__(self, responses):
        self.responses = {path: list(values) for path, values in responses.items()}
        self.requests = []

    def get_json(self, path):
        self.requests.append(path)
        values = self.responses[path]
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class SonarrAdapterTests(unittest.TestCase):
    def transport(self):
        return FakeTransport(
            {
                "/api/v3/system/status": [fixture("system-status.json")],
                "/api/v3/rootfolder": [fixture("root-folders.json")],
                "/api/v3/downloadclient": [fixture("download-clients.json")],
                "/api/v3/qualityprofile": [fixture("quality-profiles.json")],
                "/api/v3/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending&includeUnknownSeriesItems=true": [
                    fixture("queue-page-1.json")
                ],
                "/api/v3/queue?page=2&pageSize=100&sortKey=timeleft&sortDirection=ascending&includeUnknownSeriesItems=true": [
                    fixture("queue-page-2.json")
                ],
            }
        )

    def test_discovers_v3_capabilities_from_sonarr_identity(self):
        transport = self.transport()
        capabilities = SonarrAdapter(transport).discover_capabilities()

        self.assertEqual(3, capabilities.api_version)
        self.assertEqual("4.0.19.2979", capabilities.application_version)
        self.assertEqual("main", capabilities.branch)
        self.assertEqual(
            ("system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"),
            capabilities.resources,
        )
        self.assertEqual(["/api/v3/system/status"], transport.requests)
        self.assertNotIn("startupPath", json.dumps(capabilities.to_dict()))

    def test_returns_allowlisted_normalized_configuration_and_paginated_queue_summary(self):
        transport = self.transport()
        snapshot = SonarrAdapter(transport).read_snapshot()
        output = snapshot.to_dict()

        self.assertEqual("/data/media/tv", output["root_folders"][0]["path"])
        self.assertEqual("QBittorrent", output["download_clients"][0]["implementation"])
        self.assertTrue(output["download_clients"][0]["remove_completed_downloads"])
        self.assertEqual("HD-1080p", output["quality_profiles"][0]["name"])
        self.assertEqual(3, output["queue"]["total_records"])
        self.assertEqual(
            {"completed": 1, "downloading": 1, "warning": 1},
            output["queue"]["status_counts"],
        )
        rendered = json.dumps(output, sort_keys=True)
        self.assertNotIn("private-setting-canary", rendered)
        self.assertNotIn("fields", rendered)
        self.assertNotIn("title", rendered)
        self.assertEqual(6, len(transport.requests))
        self.assertTrue(all(path.startswith("/api/v3/") for path in transport.requests))

    def test_unsupported_api_and_wrong_service_identity_fail_with_typed_contextless_blockers(self):
        unsupported_transport = FakeTransport(
            {"/api/v3/system/status": [TransportFailure("RESOURCE_NOT_FOUND")]}
        )
        with self.assertRaises(SonarrAdapterFailure) as unsupported:
            SonarrAdapter(unsupported_transport).discover_capabilities()
        self.assertEqual("UNSUPPORTED_API_VERSION", unsupported.exception.code)
        self.assertIsNone(unsupported.exception.__cause__)
        self.assertIsNone(unsupported.exception.__context__)

        unauthorized_transport = FakeTransport(
            {"/api/v3/system/status": [TransportFailure("AUTH_FAILED")]}
        )
        with self.assertRaises(SonarrAdapterFailure) as unauthorized:
            SonarrAdapter(unauthorized_transport).discover_capabilities()
        self.assertEqual("AUTH_FAILED", unauthorized.exception.code)

        wrong_service = fixture("system-status.json")
        wrong_service["appName"] = "NotSonarr"
        with self.assertRaises(SonarrAdapterFailure) as identity:
            SonarrAdapter(FakeTransport({"/api/v3/system/status": [wrong_service]})).discover_capabilities()
        self.assertEqual("SERVICE_IDENTITY_INVALID", identity.exception.code)

    def test_transport_and_shape_failures_are_typed_redacted_and_contextless(self):
        marker = "private-transport-canary"
        transport_failure = TransportFailure("SERVICE_UNREACHABLE")
        transport_failure.private = marker
        with self.assertRaises(SonarrAdapterFailure) as unavailable:
            SonarrAdapter(FakeTransport({"/api/v3/system/status": [transport_failure]})).discover_capabilities()
        self.assertEqual("SERVICE_UNREACHABLE", unavailable.exception.code)
        self.assertNotIn(marker, repr(unavailable.exception) + str(unavailable.exception))
        self.assertIsNone(unavailable.exception.__cause__)
        self.assertIsNone(unavailable.exception.__context__)

        malformed = fixture("system-status.json")
        malformed["version"] = {"unexpected": marker}
        with self.assertRaises(SonarrAdapterFailure) as invalid:
            SonarrAdapter(FakeTransport({"/api/v3/system/status": [malformed]})).discover_capabilities()
        self.assertEqual("RESPONSE_SHAPE_INVALID", invalid.exception.code)
        self.assertNotIn(marker, repr(invalid.exception) + str(invalid.exception))

    def test_queue_bounds_and_inconsistent_pagination_fail_closed(self):
        status = fixture("system-status.json")
        oversized_cases = (
            {
                "page": 1,
                "pageSize": 100,
                "totalRecords": 10001,
                "records": [],
            },
            {
                "page": 1,
                "pageSize": 101,
                "totalRecords": 101,
                "records": [{"status": "queued"} for _ in range(101)],
            },
            {
                "page": 1,
                "pageSize": 100,
                "totalRecords": 101,
                "records": [{"status": "queued"} for _ in range(101)],
            },
        )
        for oversized in oversized_cases:
            responses = {
                "/api/v3/system/status": [status],
                "/api/v3/rootfolder": [[]],
                "/api/v3/downloadclient": [[]],
                "/api/v3/qualityprofile": [[]],
                "/api/v3/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending&includeUnknownSeriesItems=true": [oversized],
            }
            with self.subTest(page_size=oversized["pageSize"], records=len(oversized["records"])):
                with self.assertRaises(SonarrAdapterFailure) as failure:
                    SonarrAdapter(FakeTransport(responses)).read_snapshot()
                self.assertEqual("QUEUE_BOUNDS_INVALID", failure.exception.code)


if __name__ == "__main__":
    unittest.main()
