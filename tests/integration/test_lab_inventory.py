import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import lab as lab_script


class InventoryLabContractTests(unittest.TestCase):
    def test_lab_cli_exposes_inventory_lane(self):
        parsed = lab_script.build_parser().parse_args(["test", "inventory"])
        self.assertEqual("inventory", parsed.suite)

    def test_inventory_lane_names_all_real_adapters_and_partial_probe(self):
        source = Path("scripts/lab.py").read_text(encoding="utf-8")
        start = source.index("def test_inventory")
        end = source.index("def build_parser", start)
        block = source[start:end]
        for service in ("sonarr", "radarr", "prowlarr", "qbittorrent", "jellyfin"):
            self.assertIn(service, block)
        self.assertIn("partial", block)
        self.assertIn("StackInventoryBuilder", block)
        self.assertIn('"failure_code"', block)
        self.assertIn("PRIVATE_DATA_REDACTED", block)
        self.assertNotIn("access_path.read_text", block)
        self.assertIn('"published_ports": 0', block)


if __name__ == "__main__":
    unittest.main()
