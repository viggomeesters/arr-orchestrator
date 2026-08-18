import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import lab as lab_script


class ProwlarrLabContractTests(unittest.TestCase):
    def test_lab_cli_exposes_focused_prowlarr_adapter_lane(self):
        parsed = lab_script.build_parser().parse_args(["test", "adapter", "prowlarr"])
        self.assertEqual("adapter", parsed.suite)
        self.assertEqual("prowlarr", parsed.service)


if __name__ == "__main__":
    unittest.main()
