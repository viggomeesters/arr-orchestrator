import json
import copy
import re
import unittest
from datetime import datetime
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - explicit dependency failure
    raise RuntimeError("contract tests require the jsonschema package") from exc

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "contracts" / "v1"
FIXTURE_DIR = Path(__file__).parent / "fixtures"
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time")
def is_timezone_aware_datetime(value):
    if not isinstance(value, str):
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None

SCHEMAS = {
    "desired-state": "desired-state.schema.json",
    "capabilities": "capabilities.schema.json",
    "inventory": "inventory.schema.json",
    "findings": "findings.schema.json",
    "plan": "plan.schema.json",
    "evidence": "evidence.schema.json",
    "error": "error.schema.json",
}

POSITIVE_FIXTURES = {
    "desired-state": "desired-state.json",
    "capabilities": "capabilities.json",
    "inventory": "inventory.json",
    "findings": "findings.json",
    "plan": "plan.json",
    "evidence": "evidence.json",
    "error": "error.json",
}

NEGATIVE_FIXTURES = {
    "desired-state": ["desired-state-rejects-api-key.json", "desired-state-secret-ref-traversal.json"],
    "inventory": ["inventory-rejects-hostname.json"],
    "plan": ["plan-ambiguous-target.json", "plan-destructive-without-approval.json", "plan-rejects-secret-field.json"],
    "evidence": ["evidence-not-redacted.json"],
    "error": ["error-secret-detail.json", "error-category-mismatch.json"],
}


def reject_duplicate_members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_members)


def validator_for(schema):
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def first_operation(plan):
    operation_ref = next(iter(plan["operations"]))
    return operation_ref, plan["operations"][operation_ref]


class ContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {}
        for name, filename in SCHEMAS.items():
            path = SCHEMA_DIR / filename
            if not path.is_file():
                raise AssertionError(f"missing schema: {path.relative_to(ROOT)}")
            schema = load_json(path)
            Draft202012Validator.check_schema(schema)
            cls.schemas[name] = schema

    def test_schema_ids_are_unique_versioned_urns(self):
        ids = [schema["$id"] for schema in self.schemas.values()]
        self.assertEqual(len(ids), len(set(ids)))
        for schema_id in ids:
            self.assertRegex(schema_id, r"^urn:arr-orchestrator:schema:[a-z-]+:v1$")

    def test_plan_and_evidence_share_the_canonical_operation_reference_pattern(self):
        plan_pattern = next(iter(self.schemas["plan"]["properties"]["operations"]["patternProperties"]))
        evidence_pattern = self.schemas["evidence"]["properties"]["source"]["properties"]["operation_ref"]["pattern"]
        self.assertEqual(plan_pattern, evidence_pattern)

    def test_fixture_loader_rejects_duplicate_json_members(self):
        duplicate = '{"service":"sonarr","service":"radarr"}'
        with self.assertRaisesRegex(ValueError, "duplicate JSON member: service"):
            json.loads(duplicate, object_pairs_hook=reject_duplicate_members)

    def test_invalid_timestamps_are_rejected(self):
        for timestamp in (
            "not-a-timestamp",
            "2026-08-16 10:02:30+00:00",
            "2026-08-16T10:02:30+0000",
            "2026-08-16T10:02:30",
        ):
            with self.subTest(timestamp=timestamp):
                fixture = load_json(FIXTURE_DIR / "positive" / "evidence.json")
                fixture["observed_at"] = timestamp
                errors = list(validator_for(self.schemas["evidence"]).iter_errors(fixture))
                self.assertTrue(errors, "non-RFC3339 date-time was accepted")

    def test_positive_synthetic_fixtures_validate(self):
        for name, fixture_name in POSITIVE_FIXTURES.items():
            with self.subTest(contract=name):
                fixture = load_json(FIXTURE_DIR / "positive" / fixture_name)
                errors = list(validator_for(self.schemas[name]).iter_errors(fixture))
                self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_plan_accepts_integer_configuration_values(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        _, operation = first_operation(fixture)
        operation["change"]["before"] = 1
        operation["change"]["after"] = 2
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_private_runtime_fields_are_rejected_by_every_contract_envelope(self):
        for name, fixture_name in POSITIVE_FIXTURES.items():
            fixture = load_json(FIXTURE_DIR / "positive" / fixture_name)
            for private_field in ("hostname", "api_key", "runtime_log"):
                with self.subTest(contract=name, field=private_field):
                    candidate = copy.deepcopy(fixture)
                    candidate[private_field] = "synthetic-private-value"
                    errors = list(validator_for(self.schemas[name]).iter_errors(candidate))
                    self.assertTrue(errors, f"private field was accepted: {name}.{private_field}")

    def test_capability_limitations_are_typed_codes_not_private_free_text(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "capabilities.json")
        service = next(iter(fixture["services"].values()))
        service["limitations"] = ["private-nuc is unavailable at 10.0.0.2"]
        errors = list(validator_for(self.schemas["capabilities"]).iter_errors(fixture))
        self.assertTrue(errors, "capabilities accepted private free-text limitations")

    def test_plan_requires_every_exact_target_coordinate(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        operation_ref, operation = first_operation(fixture)
        segments = operation_ref.split(":")
        for index, target_field in enumerate(("service_id", "resource_type", "resource_key", "field")):
            with self.subTest(coordinate=target_field):
                candidate = copy.deepcopy(fixture)
                malformed_ref = ":".join(segment for position, segment in enumerate(segments) if position != index)
                candidate["operations"] = {malformed_ref: copy.deepcopy(operation)}
                errors = list(validator_for(self.schemas["plan"]).iter_errors(candidate))
                self.assertTrue(errors, f"plan accepted operation reference without {target_field}")

    def test_plan_rejects_unknown_resource_type_in_operation_reference(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        operation_ref, operation = first_operation(fixture)
        segments = operation_ref.split(":")
        segments[1] = "unknown_resource"
        fixture["operations"] = {":".join(segments): operation}
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertTrue(errors, "plan accepted an unknown resource type")

    def test_delete_cannot_downgrade_risk_or_approval(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        invalid_combinations = (("safe", True), ("risky", False), ("destructive", False))
        for risk, approval in invalid_combinations:
            with self.subTest(risk=risk, approval=approval):
                candidate = copy.deepcopy(fixture)
                _, operation = first_operation(candidate)
                operation.update(action="delete", risk=risk, requires_human_approval=approval)
                errors = list(validator_for(self.schemas["plan"]).iter_errors(candidate))
                self.assertTrue(errors, "delete accepted without destructive risk and approval")

    def test_identity_collections_are_keyed_objects(self):
        keyed_paths = (
            ("desired-state", ("properties", "services")),
            ("desired-state", ("properties", "media_roots")),
            ("capabilities", ("properties", "services")),
            ("inventory", ("properties", "services")),
            ("inventory", ("properties", "storage")),
            ("findings", ("properties", "findings")),
            ("plan", ("properties", "operations")),
        )
        for contract, path in keyed_paths:
            with self.subTest(contract=contract, path=path[-1]):
                node = self.schemas[contract]
                for segment in path:
                    node = node[segment]
                self.assertEqual("object", node.get("type"), "identity collection must be key-addressed")

    def test_ready_plan_cannot_carry_blocked_assumptions(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        fixture["blocked_assumptions"] = ["storage topology is unresolved"]
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertTrue(errors, "ready plan accepted an unresolved blocked assumption")

    def test_risky_and_destructive_operations_require_approval_records(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        _, operation = first_operation(fixture)
        operation.update(action="delete", risk="destructive", requires_human_approval=True)
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertTrue(errors, "destructive operation accepted without an approval record")

    def test_approved_destructive_operation_is_representable(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        operation_ref, operation = first_operation(fixture)
        operation.update(
            action="delete",
            risk="destructive",
            requires_human_approval=True,
            approval={
                "status": "approved",
                "authority_ref": "operator:primary",
                "evidence_ref": "approval:001",
                "decided_at": "2026-08-16T10:02:30Z",
            },
        )
        fixture["operations"] = {operation_ref.rsplit(":", 1)[0] + ":resource": operation}
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_safe_operation_can_require_approval_under_always_policy(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        _, operation = first_operation(fixture)
        operation.update(
            risk="safe",
            requires_human_approval=True,
            approval={
                "status": "approved",
                "authority_ref": "operator:primary",
                "evidence_ref": "approval:always-policy-001",
                "decided_at": "2026-08-16T10:02:30Z",
            },
        )
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_pending_approval_is_representable_but_not_ready(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        _, operation = first_operation(fixture)
        operation.update(
            risk="risky",
            requires_human_approval=True,
            approval={"status": "pending"},
        )
        fixture["status"] = "pending_approval"
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

        fixture["status"] = "ready"
        errors = list(validator_for(self.schemas["plan"]).iter_errors(fixture))
        self.assertTrue(errors, "ready plan accepted a pending approval record")

    def test_compound_credential_fields_are_rejected(self):
        fixture = load_json(FIXTURE_DIR / "positive" / "plan.json")
        operation_ref, operation = first_operation(fixture)
        prefix = operation_ref.rsplit(":", 1)[0]
        for field in (
            "auth_token",
            "settings.api_key",
            "service-password",
            "api.key",
            "private.key",
            "access.key",
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(fixture)
                candidate["operations"] = {f"{prefix}:{field}": copy.deepcopy(operation)}
                errors = list(validator_for(self.schemas["plan"]).iter_errors(candidate))
                self.assertTrue(errors, f"credential-bearing field was accepted: {field}")

    def test_private_and_ambiguous_negative_fixtures_are_rejected(self):
        for name, fixture_names in NEGATIVE_FIXTURES.items():
            validator = validator_for(self.schemas[name])
            for fixture_name in fixture_names:
                with self.subTest(contract=name, fixture=fixture_name):
                    fixture = load_json(FIXTURE_DIR / "negative" / fixture_name)
                    errors = list(validator.iter_errors(fixture))
                    self.assertTrue(errors, f"negative fixture was accepted: {fixture_name}")

    def test_every_object_schema_is_closed(self):
        def walk(node, path="$", seen=None):
            seen = seen or set()
            if id(node) in seen:
                return []
            seen.add(id(node))
            findings = []
            if isinstance(node, dict):
                if node.get("type") == "object" and node.get("additionalProperties") is not False:
                    findings.append(path)
                for key, value in node.items():
                    findings.extend(walk(value, f"{path}.{key}", seen))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    findings.extend(walk(value, f"{path}[{index}]", seen))
            return findings

        for name, schema in self.schemas.items():
            with self.subTest(contract=name):
                self.assertEqual([], walk(schema), f"open object schemas in {name}")

    def test_contract_envelope_is_versioned_and_strict(self):
        for name, schema in self.schemas.items():
            with self.subTest(contract=name):
                self.assertEqual("1.0.0", schema["properties"]["contract_version"]["const"])
                self.assertIn("artifact_kind", schema["required"])
                self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
