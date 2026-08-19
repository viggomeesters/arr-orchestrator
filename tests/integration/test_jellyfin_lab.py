import subprocess,sys,unittest
from pathlib import Path

class JellyfinLabContractTests(unittest.TestCase):
    def test_cli_routes_jellyfin_adapter_lane(self):
        run=subprocess.run([sys.executable,"scripts/lab.py","test","adapter","jellyfin","--help"],text=True,capture_output=True)
        self.assertEqual(0,run.returncode,run.stderr)
        self.assertIn("jellyfin",run.stdout.lower())

    def test_lane_reports_setup_and_single_auth_truthfully_and_guards_access_output(self):
        source=Path("scripts/lab.py").read_text()
        block=source[source.index("def test_jellyfin_adapter"):source.index("def write_private_text",source.index("def test_jellyfin_adapter"))]
        self.assertIn("bootstrap_jellyfin(Path('/run/output/jellyfin-access'))",block)
        self.assertNotIn("authenticate_jellyfin(",block)
        self.assertIn('"setup_mutation_requests": setup_posts',block)
        self.assertIn('"authentication_requests": auth_posts',block)
        self.assertIn("if opaque in encoded",block)

if __name__ == "__main__": unittest.main()
