import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = str(ROOT / "src")
COMMANDS = ("doctor", "plan", "apply", "verify", "status", "version")
sys.path.insert(0, PYTHONPATH)

from arr_orchestrator import cli as cli_module  # noqa: E402


class ArrctlCliTests(unittest.TestCase):
    def run_cli(self, *args, env=None, cwd=ROOT, pythonpath=PYTHONPATH):
        command_env = os.environ.copy()
        for name in (
            "ARR_ORCHESTRATOR_CONFIG_DIR",
            "ARR_ORCHESTRATOR_DATA_DIR",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
        ):
            command_env.pop(name, None)
        command_env["PYTHONPATH"] = str(pythonpath)
        command_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env:
            command_env.update(env)
        return subprocess.run(
            [sys.executable, "-m", "arr_orchestrator", *args],
            cwd=cwd,
            env=command_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def parse_json(self, completed):
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"stdout is not JSON (rc={completed.returncode}): "
                f"{completed.stdout!r}; stderr={completed.stderr!r}; error={exc}"
            )

    def test_help_exposes_the_complete_command_surface(self):
        completed = self.run_cli("--help")

        self.assertEqual(0, completed.returncode, completed.stderr)
        for command in COMMANDS:
            self.assertIn(command, completed.stdout)

    def test_every_command_has_deterministic_json_and_no_remote_side_effects(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                completed = self.run_cli(command, "--json")
                payload = self.parse_json(completed)

                self.assertEqual("arr-orchestrator.cli-result.v1", payload["schema"])
                self.assertEqual(command, payload["command"])
                self.assertFalse(payload["data"]["remote_side_effects"])
                self.assertEqual(completed.returncode, payload["exit_code"])
                if command == "apply":
                    self.assertEqual(5, completed.returncode)
                    self.assertEqual("blocked", payload["status"])
                    self.assertEqual("CAPABILITY_MISSING", payload["error"]["code"])
                else:
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual("ok", payload["status"])
                    self.assertNotIn("error", payload)

    def test_json_flag_works_before_or_after_the_command(self):
        before = self.run_cli("--json", "doctor")
        after = self.run_cli("doctor", "--json")

        self.assertEqual(0, before.returncode, before.stderr)
        self.assertEqual(0, after.returncode, after.stderr)
        self.assertEqual(self.parse_json(before), self.parse_json(after))

    def test_default_human_output_is_not_json(self):
        completed = self.run_cli("version")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("arrctl", completed.stdout)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(completed.stdout)

    def test_skeleton_commands_never_claim_domain_work_was_completed(self):
        expectations = {
            "doctor": ("assessment", "not_run"),
            "plan": ("plan_state", "not_generated"),
            "verify": ("assessment", "not_run"),
            "status": ("runtime_initialized", False),
        }
        for command, (field, expected) in expectations.items():
            with self.subTest(command=command):
                completed = self.run_cli(command, "--json")
                payload = self.parse_json(completed)

                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual(expected, payload["data"][field])

    def test_status_resolves_external_xdg_directories_without_creating_them(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_home = base / "config-home"
            data_home = base / "data-home"
            expected_config = config_home / "arr-orchestrator"
            expected_data = data_home / "arr-orchestrator"

            completed = self.run_cli(
                "status",
                "--json",
                env={
                    "HOME": str(base / "home"),
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_DATA_HOME": str(data_home),
                },
            )
            payload = self.parse_json(completed)

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(str(expected_config), payload["data"]["config_dir"])
            self.assertEqual(str(expected_data), payload["data"]["data_dir"])
            self.assertFalse(expected_config.exists())
            self.assertFalse(expected_data.exists())
            self.assertFalse(expected_config.is_relative_to(ROOT))
            self.assertFalse(expected_data.is_relative_to(ROOT))

    def test_explicit_runtime_directory_overrides_are_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_dir = base / "explicit-config"
            data_dir = base / "explicit-data"

            completed = self.run_cli(
                "status",
                "--json",
                env={
                    "ARR_ORCHESTRATOR_CONFIG_DIR": str(config_dir),
                    "ARR_ORCHESTRATOR_DATA_DIR": str(data_dir),
                },
            )
            payload = self.parse_json(completed)

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(str(config_dir), payload["data"]["config_dir"])
            self.assertEqual(str(data_dir), payload["data"]["data_dir"])

    def test_empty_xdg_values_are_treated_as_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            completed = self.run_cli(
                "status",
                "--json",
                env={
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": "",
                    "XDG_DATA_HOME": "",
                },
            )
            payload = self.parse_json(completed)

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(
                str(home / ".config" / "arr-orchestrator"),
                payload["data"]["config_dir"],
            )
            self.assertEqual(
                str(home / ".local" / "share" / "arr-orchestrator"),
                payload["data"]["data_dir"],
            )

    def test_higher_priority_paths_do_not_depend_on_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            cases = (
                {
                    "HOME": "relative-home",
                    "ARR_ORCHESTRATOR_CONFIG_DIR": str(base / "config"),
                    "ARR_ORCHESTRATOR_DATA_DIR": str(base / "data"),
                },
                {
                    "HOME": "relative-home",
                    "XDG_CONFIG_HOME": str(base / "xdg-config"),
                    "XDG_DATA_HOME": str(base / "xdg-data"),
                },
            )
            for env in cases:
                with self.subTest(env=sorted(env)):
                    completed = self.run_cli("status", "--json", env=env)
                    self.assertEqual(0, completed.returncode, completed.stdout)

    def test_xdg_child_symlink_into_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            config_home = base / "config-home"
            config_home.mkdir()
            (config_home / "arr-orchestrator").symlink_to(ROOT, target_is_directory=True)

            completed = self.run_cli(
                "status",
                "--json",
                env={
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_DATA_HOME": str(base / "data-home"),
                },
            )
            payload = self.parse_json(completed)

            self.assertEqual(3, completed.returncode)
            self.assertEqual("CONFIG_INVALID", payload["error"]["code"])

    def test_installed_package_rejects_explicit_path_inside_checkout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            package_root = base / "site"
            shutil.copytree(ROOT / "src" / "arr_orchestrator", package_root / "arr_orchestrator")

            completed = self.run_cli(
                "status",
                "--json",
                cwd=base,
                pythonpath=package_root,
                env={"ARR_ORCHESTRATOR_CONFIG_DIR": str(ROOT / "runtime-private")},
            )
            payload = self.parse_json(completed)

            self.assertEqual(3, completed.returncode)
            self.assertEqual("CONFIG_INVALID", payload["error"]["code"])

    def test_runtime_directory_inside_checkout_fails_closed_and_is_redacted(self):
        unsafe = ROOT / "runtime-private"
        completed = self.run_cli(
            "status",
            "--json",
            env={"ARR_ORCHESTRATOR_CONFIG_DIR": str(unsafe)},
        )
        payload = self.parse_json(completed)

        self.assertEqual(3, completed.returncode)
        self.assertEqual("error", payload["status"])
        self.assertEqual("CONFIG_INVALID", payload["error"]["code"])
        self.assertTrue(payload["error"]["redacted"])
        self.assertNotIn(str(unsafe), completed.stdout)
        self.assertNotIn(str(unsafe), completed.stderr)

    def test_invalid_json_request_uses_usage_exit_code_without_echoing_input(self):
        fake_private_input = "unknown-api-key-value"
        for args in (("--json", fake_private_input), (fake_private_input, "--json")):
            with self.subTest(args=args):
                completed = self.run_cli(*args)
                payload = self.parse_json(completed)

                self.assertEqual(2, completed.returncode)
                self.assertEqual("cli", payload["command"])
                self.assertEqual("error", payload["status"])
                self.assertNotIn("error", payload)
                self.assertEqual("USAGE_INVALID", payload["cli_error"]["code"])
                self.assertNotIn(fake_private_input, completed.stdout)
                self.assertNotIn(fake_private_input, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)

    def test_unexpected_failure_uses_cli_internal_taxonomy_without_leakage(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(cli_module, "execute", side_effect=RuntimeError("private-token")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = cli_module.main(["doctor", "--json"])
        payload = json.loads(stdout.getvalue())

        self.assertEqual(70, exit_code)
        self.assertNotIn("error", payload)
        self.assertEqual("INTERNAL_ERROR", payload["cli_error"]["code"])
        self.assertNotIn("private-token", stdout.getvalue())
        self.assertNotIn("private-token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
