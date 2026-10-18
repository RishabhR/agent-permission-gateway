"""Pydantic models mirroring policy/grant.schema.json."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scope = Literal["email:read", "email:send", "calendar:read", "calendar:write", "payments:purchase"]
SCOPES: tuple[str, ...] = (
    "email:read", "email:send", "calendar:read", "calendar:write", "payments:purchase",
)

TOOL_SCOPE: dict[str, str] = {
    "email_search": "email:read",
    "email_read": "email:read",
    "email_send": "email:send",
    "calendar_list": "calendar:read",
    "calendar_create_event": "calendar:write",
    "payments_purchase": "payments:purchase",
}

# Mirrors "x-applies-to" in the schema; enforced in code.
CONSTRAINT_APPLIES_TO: dict[str, tuple[str, ...]] = {
    "senders": ("email:read",),
    "message_ids": ("email:read",),
    "recipients": ("email:send",),
    "window": ("calendar:read", "calendar:write"),
    "attendees": ("calendar:write",),
    "merchants": ("payments:purchase",),
    "max_amount_usd": ("payments:purchase",),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(_Strict):
    start: datetime
    end: datetime


class Constraints(_Strict):
    senders: list[str] | None = None
    message_ids: list[str] | None = None
    recipients: list[str] | None = None
    window: Window | None = None
    attendees: list[str] | None = None
    merchants: list[str] | None = None
    max_amount_usd: float | None = Field(default=None, gt=0)

    def present(self) -> dict[str, Any]:
        """Constraints that are set (missing = unconstrained)."""
        return {k: getattr(self, k) for k in CONSTRAINT_APPLIES_TO if getattr(self, k) is not None}


def _check_applicability(scope: str, constraints: Constraints) -> None:
    for name in constraints.present():
        if scope not in CONSTRAINT_APPLIES_TO[name]:
            raise ValueError(f"constraint '{name}' does not apply to scope '{scope}'")


class Lifetime(_Strict):
    type: Literal["task", "session", "standing"]
    task_id: str | None = None
    session_id: str | None = None
    expires_at: datetime | None = None


class Provenance(_Strict):
    source: Literal["user_message", "user_approval", "policy_default"]
    message_ref: str | None = None
    user_text_excerpt: str | None = None
    created_at: datetime


class Grant(_Strict):
    id: str = Field(pattern=r"^g_[a-z0-9_]+$")
    scope: Scope
    constraints: Constraints = Field(default_factory=Constraints)
    lifetime: Lifetime
    max_uses: int | None = Field(default=None, ge=1)
    uses: int = Field(default=0, ge=0)
    provenance: Provenance
    rationale: str | None = None

    @model_validator(mode="after")
    def _applicable(self) -> Grant:
        _check_applicability(self.scope, self.constraints)
        return self


class ProposedGrant(_Strict):
    scope: Scope
    constraints: Constraints = Field(default_factory=Constraints)
    max_uses: int | None = Field(default=None, ge=1)
    user_text_excerpt: str
    rationale: str = Field(max_length=200)

    @model_validator(mode="after")
    def _applicable(self) -> ProposedGrant:
        _check_applicability(self.scope, self.constraints)
        return self


class GrantManifest(_Strict):
    grants: list[ProposedGrant]
    unresolved: list[str] = Field(default_factory=list)


class Action(BaseModel):
    """A normalized tool call."""
    tool: str
    scope: Scope
    args: dict[str, Any]
    derived: dict[str, Any] = Field(default_factory=dict)

    def field(self, name: str) -> Any:
        """Look up a matcher field: derived fields win over raw args."""
        if name in self.derived:
            return self.derived[name]
        return self.args.get(name)


class Ctx(BaseModel):
    task_id: str
    session_id: str
    timezone: str = "America/Chicago"


class PolicyResult(BaseModel):
    allowed: bool
    grant_id: str | None = None
    reasons: list[str]
    # Extension (schema allows extra Decision fields): True/False once executed, None if never executed.
    # False = the tool ran but the world rejected it (e.g. no matching catalog item), so it had no effect.
    executed_ok: bool | None = None


class Decision(BaseModel):
    ts: datetime
    run_id: str
    policy: str
    task_id: str
    turn: int
    tool: str
    args: dict[str, Any]
    derived: dict[str, Any] = Field(default_factory=dict)
    scope: Scope
    outcome: Literal["allowed", "denied", "escalated_approved", "escalated_denied"]
    grant_id: str | None = None
    reasons: list[str]
    # Extension (schema allows extra Decision fields): True/False once executed, None if never executed.
    # False = the tool ran but the world rejected it (e.g. no matching catalog item), so it had no effect.
    executed_ok: bool | None = None
