import pytest
from jsonschema import ValidationError

from gateway.loader import (load_fixtures, load_policies, load_schema, load_tasks, static_grants,
                            validate_manifest)

POLICY_NAMES = {"standing", "per_action_attentive", "per_action_fatigued", "session_scoped", "task_scoped"}


def test_schema_loads():
    assert "Grant" in load_schema()["$defs"]


def test_policies_parse_and_static_grants_valid():
    policies = load_policies()
    assert set(policies) == POLICY_NAMES
    assert len(static_grants("standing", policies["standing"], "2026-10-19T09:00:00-05:00")) == 5


def test_fixtures_parse():
    fx = load_fixtures()
    assert fx["clock"]["timezone"] == "America/Chicago"
    assert len(fx["inbox"]) == 13 and len(fx["arrivals"]) == 3 and len(fx["calendar"]) == 9


def test_tasks_parse_and_all_matchers_valid():
    tasks = load_tasks()
    assert len(tasks) == 18
    assert sum(len(t["turns"]) for t in tasks) == 22


def test_task_arrivals_reference_fixture_arrivals():
    ids = {a["id"] for a in load_fixtures()["arrivals"]}
    for t in load_tasks():
        for e in t.get("arrivals", {}).get("emails", []):
            assert e in ids


def test_manifest_validation():
    ok = {"grants": [{"scope": "email:send", "constraints": {"recipients": ["priya.shah@friends.example"]},
                      "user_text_excerpt": "Reply to Priya", "rationale": "send reply"}], "unresolved": []}
    assert validate_manifest(ok).grants[0].scope == "email:send"
    with pytest.raises(ValidationError):
        validate_manifest({"grants": [{"scope": "email:delete", "user_text_excerpt": "x", "rationale": "y"}]})
    with pytest.raises(ValidationError):  # missing provenance excerpt
        validate_manifest({"grants": [{"scope": "email:send", "rationale": "y"}]})
