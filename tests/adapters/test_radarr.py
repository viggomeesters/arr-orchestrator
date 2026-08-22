import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.adapters.radarr import RadarrAdapter, RadarrAdapterFailure
from arr_orchestrator.transport import TransportFailure


FIXTURES = ROOT / "tests" / "fixtures" / "radarr"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    def __init__(self, responses):
        self.responses = {path: list(values) for path, values in responses.items()}
        self.requests = []
        self.projections = []

    def get_json(self, path):
        self.requests.append(path)
        value = self.responses[path].pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def get_json_list_fields(self, path, fields):
        self.projections.append((path, fields))
        values = self.get_json(path)
        return [{field: item[field] for field in fields if field in item} for item in values]


class RadarrAdapterTests(unittest.TestCase):
    def transport(self):
        return FakeTransport(
            {
                "/api/v3/system/status": [fixture("system-status.json")],
                "/api/v3/rootfolder": [fixture("root-folders.json")],
                "/api/v3/downloadclient": [fixture("download-clients.json")],
                "/api/v3/qualityprofile": [fixture("quality-profiles.json")],
                "/api/v3/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending": [
                    fixture("queue-page-1.json")
                ],
                "/api/v3/queue?page=2&pageSize=100&sortKey=timeleft&sortDirection=ascending": [
                    fixture("queue-page-2.json")
                ],
            }
        )

    def test_discovers_v3_capabilities_from_radarr_identity(self):
        transport = self.transport()
        capabilities = RadarrAdapter(transport).discover_capabilities()

        self.assertEqual(3, capabilities.api_version)
        self.assertEqual("6.3.0.10514", capabilities.application_version)
        self.assertEqual("master", capabilities.branch)
        self.assertEqual(
            ("system_status", "root_folders", "download_clients", "quality_profiles", "queue_summary"),
            capabilities.resources,
        )
        self.assertEqual(["/api/v3/system/status"], transport.requests)
        self.assertNotIn("startupPath", json.dumps(capabilities.to_dict()))

    def test_returns_allowlisted_normalized_configuration_and_paginated_queue_summary(self):
        transport = self.transport()
        output = RadarrAdapter(transport).read_snapshot().to_dict()

        self.assertEqual("/data/media/movies", output["root_folders"][0]["path"])
        self.assertEqual("QBittorrent", output["download_clients"][0]["implementation"])
        self.assertTrue(output["download_clients"][0]["remove_completed_downloads"])
        self.assertEqual("HD-1080p", output["quality_profiles"][0]["name"])
        self.assertEqual(3, output["queue"]["total_records"])
        self.assertEqual(
            {"completed": 1, "downloading": 1, "warning": 1},
            output["queue"]["status_counts"],
        )
        rendered = json.dumps(output, sort_keys=True)
        for forbidden in (
            "private-radarr-setting-canary",
            "private-download-id",
            "/private/path",
            "fields",
            "title",
            "movieId",
            "downloadId",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(6, len(transport.requests))
        self.assertTrue(all(path.startswith("/api/v3/") for path in transport.requests))
        self.assertEqual(
            [
                (
                    "/api/v3/downloadclient",
                    (
                        "id",
                        "name",
                        "implementation",
                        "protocol",
                        "enable",
                        "priority",
                        "removeCompletedDownloads",
                        "removeFailedDownloads",
                    ),
                )
            ],
            transport.projections,
        )

    def test_unsupported_api_wrong_identity_and_later_404_keep_truthful_typed_failures(self):
        with self.assertRaises(RadarrAdapterFailure) as unsupported:
            RadarrAdapter(
                FakeTransport({"/api/v3/system/status": [TransportFailure("RESOURCE_NOT_FOUND")]})
            ).discover_capabilities()
        self.assertEqual("UNSUPPORTED_API_VERSION", unsupported.exception.code)
        self.assertIsNone(unsupported.exception.__cause__)
        self.assertIsNone(unsupported.exception.__context__)

        wrong_service = fixture("system-status.json")
        wrong_service["appName"] = "NotRadarr"
        with self.assertRaises(RadarrAdapterFailure) as identity:
            RadarrAdapter(FakeTransport({"/api/v3/system/status": [wrong_service]})).discover_capabilities()
        self.assertEqual("SERVICE_IDENTITY_INVALID", identity.exception.code)

        transport = self.transport()
        transport.responses["/api/v3/rootfolder"] = [TransportFailure("RESOURCE_NOT_FOUND")]
        with self.assertRaises(RadarrAdapterFailure) as missing_resource:
            RadarrAdapter(transport).read_snapshot()
        self.assertEqual("RESOURCE_NOT_FOUND", missing_resource.exception.code)

    def test_transport_and_shape_failures_are_typed_redacted_and_contextless(self):
        marker = "private-radarr-transport-canary"
        transport_failure = TransportFailure("SERVICE_UNREACHABLE")
        transport_failure.private = marker
        with self.assertRaises(RadarrAdapterFailure) as unavailable:
            RadarrAdapter(FakeTransport({"/api/v3/system/status": [transport_failure]})).discover_capabilities()
        self.assertEqual("SERVICE_UNREACHABLE", unavailable.exception.code)
        self.assertNotIn(marker, repr(unavailable.exception) + str(unavailable.exception))
        self.assertIsNone(unavailable.exception.__cause__)
        self.assertIsNone(unavailable.exception.__context__)

        download_failure = TransportFailure("PRIVATE_DATA_REDACTED")
        download_failure.private = marker
        with self.assertRaises(RadarrAdapterFailure) as download_unavailable:
            RadarrAdapter(
                FakeTransport({"/api/v3/downloadclient": [download_failure]})
            )._download_clients()
        self.assertEqual("PRIVATE_DATA_REDACTED", download_unavailable.exception.code)
        self.assertIsNone(download_unavailable.exception.__cause__)
        self.assertIsNone(download_unavailable.exception.__context__)

        malformed = fixture("system-status.json")
        malformed["version"] = {"unexpected": marker}
        with self.assertRaises(RadarrAdapterFailure) as invalid:
            RadarrAdapter(FakeTransport({"/api/v3/system/status": [malformed]})).discover_capabilities()
        self.assertEqual("RESPONSE_SHAPE_INVALID", invalid.exception.code)
        self.assertNotIn(marker, repr(invalid.exception) + str(invalid.exception))

    def test_queue_boundaries_fail_closed(self):
        for payload in (
            {"page": 1, "pageSize": 100, "totalRecords": 10001, "records": []},
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
        ):
            transport = self.transport()
            transport.responses[
                "/api/v3/queue?page=1&pageSize=100&sortKey=timeleft&sortDirection=ascending"
            ] = [payload]
            with self.subTest(page_size=payload["pageSize"], records=len(payload["records"])):
                with self.assertRaises(RadarrAdapterFailure) as failure:
                    RadarrAdapter(transport).read_snapshot()
                self.assertEqual("QUEUE_BOUNDS_INVALID", failure.exception.code)


if __name__ == "__main__":
    unittest.main()
