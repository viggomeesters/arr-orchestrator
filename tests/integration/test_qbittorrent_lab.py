import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import lab as lab_script


class QbittorrentLabContractTests(unittest.TestCase):
    def test_lab_cli_exposes_qbittorrent_adapter_lane(self):
        parsed = lab_script.build_parser().parse_args(["test", "adapter", "qbittorrent"])
        self.assertEqual("qbittorrent", parsed.service)


if __name__ == "__main__":
    unittest.main()
