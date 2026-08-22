from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import scripts.lab as lab


class LabDoctorContractTests(unittest.TestCase):
    def test_parser_accepts_doctor_suite(self):
        args = lab.build_parser().parse_args(["test", "doctor"])
        self.assertEqual("doctor", args.suite)

    def test_main_routes_doctor_suite(self):
        with mock.patch.object(lab, "test_doctor", return_value=0) as doctor:
            self.assertEqual(0, lab.main(["test", "doctor"]))
        doctor.assert_called_once_with()

    def test_doctor_lane_is_bounded_and_privacy_safe_by_construction(self):
        source = (ROOT / "scripts" / "lab.py").read_text(encoding="utf-8")
        start = source.index("def test_doctor()")
        end = source.index("\ndef render()", start)
        lane = source[start:end]

        self.assertIn("DoctorEngine", lane)
        self.assertIn("converge_radarr_hardlink_topology", lane)
        self.assertIn("controller_scenario_command", lane)
        self.assertIn("compose_down_command", lane)
        self.assertIn("assert_no_secret_exposure", lane)
        self.assertNotIn("docker system prune", lane)
        self.assertNotIn("reveal()", lane)


class LiveDoctorTests(unittest.TestCase):
    def test_live_doctor_lane(self):
        if "--live" not in sys.argv:
            self.skipTest("pass --live after unittest arguments to run the isolated Docker lane")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "lab.py"), "test", "doctor"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('"schema":"arr-orchestrator.lab-doctor-run.v1"', result.stdout)
        self.assertIn('"ok":true', result.stdout)


if __name__ == "__main__":
    unittest.main()
