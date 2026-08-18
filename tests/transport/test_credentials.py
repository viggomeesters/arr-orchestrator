import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.credentials import CredentialError, FileCredentialResolver


class CredentialResolverTests(unittest.TestCase):
    def make_root(self, directory):
        root = Path(directory) / "secrets"
        root.mkdir(mode=0o700)
        return root

    def write_secret(self, root, name="token", value="synthetic-token"):
        path = root / name
        path.write_text(value)
        path.chmod(0o600)
        return path

    def test_resolves_one_private_regular_file_without_repr_leakage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            self.write_secret(root, value="synthetic-token\n")
            credential = FileCredentialResolver(root, expected_uid=os.getuid()).resolve("file:token")
            self.assertEqual("synthetic-token", credential.reveal())
            self.assertNotIn("synthetic-token", repr(credential))
            self.assertNotIn("synthetic-token", str(credential))

    def test_rejects_traversal_symlinks_types_modes_owner_and_header_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_root(directory)
            outside = base / "outside"
            outside.write_text("outside")
            outside.chmod(0o600)
            resolver = FileCredentialResolver(root, expected_uid=os.getuid())
            cases = []
            cases.append(("file:../outside", None))
            (root / "link").symlink_to(outside)
            cases.append(("file:link", None))
            (root / "directory").mkdir()
            cases.append(("file:directory", None))
            os.mkfifo(root / "fifo", mode=0o600)
            cases.append(("file:fifo", None))
            loose = self.write_secret(root, "loose")
            loose.chmod(0o644)
            cases.append(("file:loose", None))
            multiline = self.write_secret(root, "multiline", "one\ntwo")
            cases.append(("file:multiline", None))
            nul = self.write_secret(root, "nul", "one\x00two")
            cases.append(("file:nul", None))
            carriage = self.write_secret(root, "carriage", "one\rtwo")
            cases.append(("file:carriage", None))
            unicode_nel = self.write_secret(root, "unicode-nel", "one\u0085two")
            cases.append(("file:unicode-nel", None))
            unicode_line = self.write_secret(root, "unicode-line", "one\u2028two")
            cases.append(("file:unicode-line", None))
            for ref, _ in cases:
                with self.subTest(ref=ref), self.assertRaises(CredentialError):
                    resolver.resolve(ref)
            wrong_owner = self.write_secret(root, "owner")
            with self.assertRaises(CredentialError):
                FileCredentialResolver(root, expected_uid=os.getuid() + 1).resolve("file:owner")

    def test_rejects_symlinked_parent_component_and_hardlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = self.make_root(directory)
            self.write_secret(real)
            linked = base / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(CredentialError):
                FileCredentialResolver(linked, expected_uid=os.getuid()).resolve("file:token")
            os.link(real / "token", real / "token-hardlink")
            with self.assertRaises(CredentialError):
                FileCredentialResolver(real, expected_uid=os.getuid()).resolve("file:token")

    def test_parent_swap_between_preflight_and_file_open_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_root(directory)
            outside = base / "outside"
            outside.mkdir()
            self.write_secret(root, "entry", "trusted")
            self.write_secret(outside, "entry", "outside")
            resolver = FileCredentialResolver(root, expected_uid=os.getuid())
            original_open = os.open
            swapped = False

            def racing_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == "entry" and not swapped:
                    swapped = True
                    root.rename(base / "trusted-old")
                    root.symlink_to(outside, target_is_directory=True)
                return original_open(path, flags, *args, **kwargs)

            os.open = racing_open
            try:
                credential = resolver.resolve("file:entry")
            finally:
                os.open = original_open
            self.assertEqual("trusted", credential.reveal())
            self.assertNotEqual("outside", credential.reveal())

    def test_replaced_real_root_is_rejected_even_with_valid_file_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_root(directory)
            self.write_secret(root, "entry", "trusted")
            resolver = FileCredentialResolver(root, expected_uid=os.getuid())
            root.rename(base / "trusted-old")
            root.mkdir(mode=0o700)
            self.write_secret(root, "entry", "replacement")
            with self.assertRaises(CredentialError):
                resolver.resolve("file:entry")


if __name__ == "__main__":
    unittest.main()
