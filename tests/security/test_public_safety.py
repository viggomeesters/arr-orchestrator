import base64
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from scripts.check_public_safety import scan_tree


class PublicSafetyTests(unittest.TestCase):
    def scan(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tracked = []
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                tracked.append(name)
            return scan_tree(root, tracked)

    def test_clean_synthetic_contract_tree_passes(self):
        self.assertEqual([], self.scan({"docs/example.json": '{"secret_ref":"file:sonarr/api-key","host":"sonarr"}'}))

    def test_generated_service_state_and_secret_files_are_rejected(self):
        for name in (
            "lab/runtime/config/sonarr/config.xml",
            "lab/state/qbittorrent/qBittorrent.conf",
            "lab/secrets/api-key",
            "lab/generated/service.sqlite",
            "lab/evidence/runtime.log",
        ):
            with self.subTest(name=name):
                self.assertTrue(self.scan({name: "synthetic"}))

    def test_service_config_filenames_are_rejected_outside_runtime_directories(self):
        for name in ("fixtures/config.xml", "fixtures/qBittorrent.conf", "fixtures/.arr-orchestrator-lab"):
            with self.subTest(name=name):
                self.assertTrue(self.scan({name: "synthetic"}))

    def test_private_network_and_host_paths_are_rejected(self):
        for content in (
            "http://192.168.1.50:8989",
            "host=10.0.0.8",
            "http://media-nuc.local:8989",
            "hostname=private-nas.lan",
            "root=/mnt/c/Users/private/media",
            "root=/home/alice/media",
        ):
            with self.subTest(content=content):
                self.assertTrue(self.scan({"docs/leak.txt": content}))

    def test_raw_and_encoded_secret_canaries_are_rejected(self):
        canary = "ARR_ORCHESTRATOR_SECRET_CANARY_1234567890"
        fully_percent_encoded = "".join(f"%{ord(character):02X}" for character in canary)
        variants = (canary, base64.b64encode(canary.encode()).decode(), urllib.parse.quote(canary), fully_percent_encoded)
        for content in variants:
            with self.subTest(content=content):
                self.assertTrue(self.scan({"evidence/proof.json": content}))

    def test_common_token_private_key_and_credentials_file_leaks_are_rejected(self):
        leaks = {
            "evidence/token.txt": "ghp_" + "A" * 36,
            "fixtures/id_rsa": "-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic\n-----END OPENSSH PRIVATE KEY-----",
            "fixtures/credentials.json": '{"synthetic":"fixture"}',
        }
        for name, content in leaks.items():
            with self.subTest(name=name):
                self.assertTrue(self.scan({name: content}))

    def test_additional_private_artifact_classes_are_rejected(self):
        leaks = {
            ".env": "SYNTHETIC=fixture",
            "fixtures/secrets.json": '{"synthetic":"fixture"}',
            "fixtures/sample.torrent": "synthetic",
            "fixtures/sample.nzb": "synthetic",
            "evidence/private-ipv6.txt": "endpoint=http://fd00::1234",
            "evidence/docker-inspect.json": '{"Id":"sha256:synthetic","Mounts":[]}',
        }
        for name, content in leaks.items():
            with self.subTest(name=name):
                self.assertTrue(self.scan({name: content}))
        self.assertEqual([], self.scan({".env.example": "SYNTHETIC=placeholder"}))

    def test_public_url_is_allowed_but_raw_credential_assignment_is_rejected(self):
        self.assertEqual([], self.scan({".github/config.yml": "url: https://github.com/example/project"}))
        self.assertEqual([], self.scan({"docs/link.txt": "https://example.com/home/alice/project"}))
        self.assertTrue(self.scan({"docs/leak.txt": "api_key=real-looking-secret-value"}))

    def test_normal_documentation_terms_do_not_trigger_false_positive(self):
        errors = self.scan(
            {
                "docs/security.md": "The lab rejects private IP addresses, Docker inspect secrets, and runtime logs.",
                "pyproject.toml": 'classifiers = ["Development Status :: 2 - Pre-Alpha", "Programming Language :: Python :: 3.11"]',
                "schemas/example.json": '{"gateway_mode_ipv4":"isolated","published_ports":[]}',
            }
        )
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
