"""Load and validate the four source-of-truth files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .matching import validate_matcher
from .models import Grant, GrantManifest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "policy" / "grant.schema.json"
POLICIES_PATH = ROOT / "policy" / "policies.yaml"
FIXTURES_PATH = ROOT / "eval" / "fixtures.yaml"
TASKS_PATH = ROOT / "eval" / "tasks.yaml"


def _yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def _validator(defn: str) -> jsonschema.Draft202012Validator:
    schema = load_schema()
    return jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{defn}"})


def validate_manifest(manifest: dict[str, Any]) -> GrantManifest:
    """Validate planner output against the schema (then pydantic). Raises on failure."""
    _validator("GrantManifest").validate(manifest)
    return GrantManifest.model_validate(manifest)


def validate_grant(grant: dict[str, Any]) -> Grant:
    _validator("Grant").validate(grant)
    return Grant.model_validate(grant)


def load_fixtures() -> dict[str, Any]:
    return _yaml(FIXTURES_PATH)


def static_grants(name: str, cfg: dict[str, Any], created_at: str) -> list[Grant]:
    """Materialize a policy's static_grants as schema-valid policy_default Grants."""
    out = []
    for i, sg in enumerate(cfg.get("static_grants", []), start=1):
        out.append(validate_grant({
            "id": f"g_static_{name}_{i}",
            "scope": sg["scope"],
            "constraints": sg.get("constraints", {}),
            "lifetime": sg["lifetime"],
            "provenance": {"source": "policy_default", "created_at": created_at},
        }))
    return out


def load_policies(created_at: str | None = None) -> dict[str, dict[str, Any]]:
    """Policies keyed by name; every static grant is validated against the schema."""
    created_at = created_at or load_fixtures()["clock"]["now"]
    policies = _yaml(POLICIES_PATH)["policies"]
    for name, cfg in policies.items():
        static_grants(name, cfg, created_at)  # raises if invalid
        if cfg.get("planner") and cfg.get("grant_lifetime") not in ("task", "session"):
            raise ValueError(f"policy {name}: planner policies need grant_lifetime task|session")
        if cfg.get("on_no_grant") not in ("deny", "escalate"):
            raise ValueError(f"policy {name}: on_no_grant must be deny|escalate")
    return policies


def load_tasks() -> list[dict[str, Any]]:
    """Tasks with every matcher syntax-checked (unknown op/field/tool raises)."""
    tasks = _yaml(TASKS_PATH)["tasks"]
    for t in tasks:
        for turn in t["turns"]:
            for m in [*turn.get("allowed", []), *turn.get("required", [])]:
                validate_matcher(m)
            for atk in turn.get("attacks", []):
                validate_matcher(atk["match"])
    return tasks
