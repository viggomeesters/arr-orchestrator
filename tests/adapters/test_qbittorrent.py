import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from arr_orchestrator.adapters.qbittorrent import QbittorrentAdapter, QbittorrentAdapterFailure
from arr_orchestrator.transport import TransportFailure

FIXTURES = ROOT / "tests" / "fixtures" / "qbittorrent"


class FakeTransport:
    def __init__(self, text=None, data=None, failure=None):
        self.text = {k: list(v) for k, v in (text or {}).items()}
        self.data = {k: list(v) for k, v in (data or {}).items()}
        self.failure = failure
        self.calls = []
    def get_text(self, path):
        self.calls.append(("GET", path))
        if self.failure: raise self.failure
        return self.text[path].pop(0)
    def get_json(self, path):
        self.calls.append(("GET", path))
        if self.failure: raise self.failure
        return self.data[path].pop(0)


def fixture(name): return json.loads((FIXTURES / name).read_text())


class QbittorrentAdapterTests(unittest.TestCase):
    def transport(self):
        return FakeTransport(
            text={"/api/v2/app/version": ["5.2.3"], "/api/v2/app/webapiVersion": ["2.11.4"]},
            data={"/api/v2/torrents/categories": [fixture("categories.json")], "/api/v2/sync/maindata?rid=0": [fixture("main-data.json")]},
        )

    def test_normalizes_categories_and_queue_without_download_identity(self):
        transport = self.transport()
        payload = QbittorrentAdapter(transport).read_snapshot().to_dict()
        self.assertEqual("5.2.3", payload["capabilities"]["application_version"])
        self.assertEqual("2.11.4", payload["capabilities"]["webapi_version"])
        self.assertEqual([
            {"name": "arr-lab", "save_path": "/data/downloads/arr-lab"},
            {"name": "manual", "save_path": "/data/downloads/manual"},
        ], payload["categories"])
        self.assertEqual({"total": 2, "state_counts": {"downloading": 1, "pausedDL": 1}, "category_counts": {"arr-lab": 1, "manual": 1}}, payload["queue"])
        encoded = repr(payload)
        for private in ("Private Download", "private-hash", "/private/item"):
            self.assertNotIn(private, encoded)
        self.assertEqual([("GET", "/api/v2/app/version"), ("GET", "/api/v2/app/webapiVersion"), ("GET", "/api/v2/torrents/categories"), ("GET", "/api/v2/sync/maindata?rid=0")], transport.calls)

    def test_transport_failures_are_contextless_and_truthful(self):
        for code in ("AUTH_FAILED", "RESOURCE_NOT_FOUND", "SERVICE_UNREACHABLE"):
            with self.subTest(code=code), self.assertRaises(QbittorrentAdapterFailure) as caught:
                QbittorrentAdapter(FakeTransport(failure=TransportFailure(code, retryable=True))).read_snapshot()
            self.assertEqual(code, caught.exception.code)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

    def test_rejects_malformed_versions_categories_and_queue(self):
        cases=[]
        t=self.transport(); t.text["/api/v2/app/version"]=["not a version"]; cases.append(t)
        t=self.transport(); t.text["/api/v2/app/version"]=["5.2.3_."]; cases.append(t)
        t=self.transport(); t.data["/api/v2/torrents/categories"]=[{"x":{"name":"x","savePath":"relative"}}]; cases.append(t)
        t=self.transport(); t.data["/api/v2/sync/maindata?rid=0"]=[{}]; cases.append(t)
        t=self.transport(); t.data["/api/v2/sync/maindata?rid=0"]=[{"torrents":{"h":{"state":1,"category":"x"}}}]; cases.append(t)
        t=self.transport(); t.data["/api/v2/sync/maindata?rid=0"]=[{"torrents":{"h":{"state":"   ","category":"x"}}}]; cases.append(t)
        t=self.transport(); t.data["/api/v2/sync/maindata?rid=0"]=[{"torrents":{"h":{"state":"ok","category":"bad\u007f"}}}]; cases.append(t)
        for transport in cases:
            with self.assertRaises(QbittorrentAdapterFailure) as caught:
                QbittorrentAdapter(transport).read_snapshot()
            self.assertEqual("RESPONSE_SHAPE_INVALID", caught.exception.code)

    def test_bounds_categories_and_queue(self):
        t=self.transport(); t.data["/api/v2/torrents/categories"]=[{str(i):{"name":str(i),"savePath":"/data"} for i in range(10001)}]
        with self.assertRaisesRegex(QbittorrentAdapterFailure, "RESPONSE_BOUNDS_INVALID"):
            QbittorrentAdapter(t).read_snapshot()
        t=self.transport(); t.data["/api/v2/sync/maindata?rid=0"]=[{"torrents":{str(i):{"state":"x","category":""} for i in range(10001)}}]
        with self.assertRaisesRegex(QbittorrentAdapterFailure, "RESPONSE_BOUNDS_INVALID"):
            QbittorrentAdapter(t).read_snapshot()

    def test_accepts_official_sparse_empty_full_update(self):
        transport = self.transport()
        transport.data["/api/v2/sync/maindata?rid=0"] = [
            {"rid": 1, "full_update": True, "server_state": {"connection_status": "disconnected"}}
        ]
        self.assertEqual(0, QbittorrentAdapter(transport).read_snapshot().queue.total)


if __name__ == "__main__": unittest.main()
