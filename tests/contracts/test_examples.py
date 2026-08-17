import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "desired-state.example.json"
SCHEMA = ROOT / "schemas" / "contracts" / "v1" / "desired-state.schema.json"


class DesiredStateExampleTests(unittest.TestCase):
    def test_json_example_validates_against_canonical_schema(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(example), key=lambda item: list(item.path))
        self.assertEqual([], [error.message for error in errors])

    def test_legacy_yaml_example_is_removed(self):
        self.assertFalse((ROOT / "examples" / "desired-state.example.yaml").exists())

    def test_example_uses_only_synthetic_file_secret_references(self):
        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        encoded = json.dumps(example, sort_keys=True)
        self.assertNotIn("url_env", encoded)
        self.assertNotIn("api_key_env", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("password", encoded)
        for service in example["services"].values():
            self.assertRegex(service["secret_ref"], r"^file:[a-z0-9-]+/[a-z0-9-]+$")


if __name__ == "__main__":
    unittest.main()
