import importlib.util
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.config import ServiceEndpoint
from arr_orchestrator.credentials import SecretValue
from arr_orchestrator.transport import ReadOnlyHttpTransport, TransportFailure, TransportPolicy
from scripts import lab as lab_script


def load_fault_api():
    path = ROOT / "lab/services/fault-api/server.py"
    spec = importlib.util.spec_from_file_location("transport_fault_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Resolver:
    def resolve(self, _): return SecretValue("synthetic-token")


class TransportFaultApiTests(unittest.TestCase):
    def test_lab_cli_exposes_transport_suite(self):
        parsed = lab_script.build_parser().parse_args(["test", "transport"])
        self.assertEqual("transport", parsed.suite)

    @classmethod
    def setUpClass(cls):
        cls.fault = load_fault_api()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.fault.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=2)

    def client(self, **kwargs):
        return ReadOnlyHttpTransport(
            ServiceEndpoint("fault-api", self.base_url, "file:fault-api-token"), Resolver(),
            policy=TransportPolicy(retry_backoff_seconds=0, **kwargs),
        )

    def test_healthy_and_adversarial_faults_are_typed(self):
        self.fault.STATE.reset()
        self.assertEqual("ok", self.client().get_json("/api/v1/probe")["status"])
        expected = {
            "unavailable": "SERVICE_UNREACHABLE",
            "malformed-json": "JSON_INVALID",
            "timeout": "DEADLINE_EXCEEDED",
        }
        for scenario, code in expected.items():
            with self.subTest(scenario=scenario):
                self.fault.STATE.set(scenario)
                with self.assertRaises(TransportFailure) as caught:
                    self.client(deadline_seconds=0.2, max_attempts=1).get_json("/api/v1/probe")
                self.assertEqual(code, caught.exception.code)
        self.fault.STATE.reset()


if __name__ == "__main__": unittest.main()
