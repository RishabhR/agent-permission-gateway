"""Gateway: grant store, lifetimes, escalation, audit, capabilities()."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog
from .loader import static_grants
from .matching import normalize, principal_matches
from .models import Action, Ctx, Decision, Grant, GrantManifest, ProposedGrant
from .policy import evaluate
from .world import World

Approver = Callable[[Action], bool]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class TurnStart:
    minted: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # provenance violations
    deferred: list[str] = field(default_factory=list)  # Fix A: write grants dropped for a missing key constraint
    minted_grants: list[dict[str, Any]] = field(default_factory=list)  # the proposed grants actually minted
    prompts: int = 0


# Fix A: the constraint that "keys" each write scope. Constraint present (even `[]`) = keyed.
WRITE_KEY_CONSTRAINT = {"email:send": "recipients", "payments:purchase": "merchants",
                        "calendar:write": "attendees"}


DENIAL_STYLES = ("terminal", "recoverable")


def denial_message(reason: str) -> str:
    """Terminal style (baseline): byte-identical to the original text."""
    return f"PERMISSION_DENIED: {reason}. Do not retry; tell the user what you need permission for."


def recoverable_denial_message(reason: str, scope: str, capabilities: list[str]) -> str:
    """Fix B: same for every policy; shows the live permissions for the denied scope."""
    have = "; ".join(capabilities) if capabilities else "none"
    return (f"PERMISSION_DENIED: {reason}. Your current permissions for {scope}: {have}. "
            "You may retry with arguments inside these permissions, or tell the user what additional "
            "permission you need.")


class Gateway:
    def __init__(self, world: World, policy_name: str, policy_cfg: dict[str, Any],
                 approver: Approver, audit: AuditLog, run_id: str = "run"):
        self.world, self.policy_name, self.cfg = world, policy_name, policy_cfg
        self.approver, self.audit, self.run_id = approver, audit, run_id
        style = policy_cfg.get("denial_style", "terminal")
        if style not in DENIAL_STYLES:
            raise ValueError(f"denial_style must be one of {DENIAL_STYLES}, got {style!r}")
        self.grants: list[Grant] = static_grants(policy_name, policy_cfg, world.now.isoformat())
        self.ctx: Ctx | None = None
        self.turn = 0
        self.user_text = ""
        self.turn_prompts = 0
        self.task_prompts = 0
        self._seq = 0

    # ---- turn lifecycle ----
    def begin_turn(self, task_id: str, session_id: str, turn: int, user_text: str,
                   manifest: GrantManifest | None = None) -> TurnStart:
        self.ctx = Ctx(task_id=task_id, session_id=session_id, timezone=self.world.timezone)
        self.turn, self.user_text, self.turn_prompts = turn, user_text, 0
        start = TurnStart()
        if self.cfg.get("planner") and manifest is not None:
            for pg in manifest.grants:
                if not pg.user_text_excerpt or pg.user_text_excerpt not in user_text:
                    self.audit.event("provenance_violation", run_id=self.run_id, task_id=task_id, turn=turn,
                                     scope=pg.scope, user_text_excerpt=pg.user_text_excerpt)
                    start.dropped.append(pg.user_text_excerpt)
                    continue
                key = WRITE_KEY_CONSTRAINT.get(pg.scope)
                if self.cfg.get("defer_unkeyed_writes", False) and key and getattr(pg.constraints, key) is None:
                    # Fix A: don't widen. The call escalates later, when concrete args are known.
                    self.audit.event("deferred_unkeyed", run_id=self.run_id, task_id=task_id, turn=turn,
                                     scope=pg.scope, missing=key, rationale=pg.rationale,
                                     unresolved=list(manifest.unresolved))
                    start.deferred.append(pg.scope)
                    continue
                start.minted.append(self._mint_from_manifest(pg, task_id, session_id, turn, user_text))
                start.minted_grants.append(pg.model_dump(mode="json", exclude_none=True))
            if start.minted:  # the (post-drop) manifest shown for approval: one prompt
                self._prompt()
        start.prompts = self.turn_prompts
        return start

    def end_turn(self) -> None:
        """Task-lifetime grants (including one-shot approvals) die when the turn ends."""
        for g in self.grants:
            if g.lifetime.type == "task" and g.lifetime.expires_at is None:
                g.lifetime.expires_at = self.world.now

    def _prompt(self) -> None:
        self.turn_prompts += 1
        self.task_prompts += 1

    def _next_id(self, tag: str) -> str:
        self._seq += 1
        assert self.ctx
        return f"g_{self.ctx.task_id.lower()}_t{self.turn}_{tag}{self._seq}"

    def _mint_from_manifest(self, pg: ProposedGrant, task_id: str, session_id: str, turn: int,
                            user_text: str) -> str:
        kind = self.cfg["grant_lifetime"]
        lifetime = {"type": "task", "task_id": task_id} if kind == "task" else {"type": "session", "session_id": session_id}
        g = Grant.model_validate({
            "id": self._next_id("m"), "scope": pg.scope, "constraints": pg.constraints.model_dump(mode="json", exclude_none=True),
            "lifetime": lifetime, "max_uses": pg.max_uses, "rationale": pg.rationale,
            "provenance": {"source": "user_message", "message_ref": f"{task_id}/turn{turn}",
                           "user_text_excerpt": pg.user_text_excerpt, "created_at": self.world.now.isoformat()},
        })
        self.grants.append(g)
        return g.id

    def _mint_approval(self, action: Action) -> Grant:
        assert self.ctx
        g = Grant.model_validate({
            "id": self._next_id("a"), "scope": action.scope, "max_uses": 1, "uses": 1,
            "lifetime": {"type": "task", "task_id": self.ctx.task_id},
            "provenance": {"source": "user_approval", "message_ref": f"{self.ctx.task_id}/turn{self.turn}",
                           "created_at": self.world.now.isoformat()},
            "rationale": f"user approved one {action.tool} call",
        })
        self.grants.append(g)
        return g

    # ---- tool calls ----
    def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        if self.ctx is None:
            raise RuntimeError("begin_turn() must be called before call()")
        action = normalize(tool, args, self.world.senders())
        result = evaluate(action, self.grants, self.world.now, self.ctx)
        reasons = list(result.reasons)
        grant_id = result.grant_id

        if result.allowed:
            outcome = "allowed"
            next(g for g in self.grants if g.id == grant_id).uses += 1
        elif self.cfg["on_no_grant"] == "escalate":
            self._prompt()
            if self.approver(action):
                outcome, g = "escalated_approved", self._mint_approval(action)
                grant_id = g.id
                reasons.append(f"{g.id}: user approved this action")
            else:
                outcome = "escalated_denied"
                reasons.append("user declined this action")
        else:
            outcome = "denied"

        if outcome in ("allowed", "escalated_approved"):
            content = self._execute(action, grant_id if outcome == "allowed" else None)
            ok = not (isinstance(json.loads(content), dict) and "error" in json.loads(content))
            self._log(action, outcome, grant_id, reasons, ok)
            return ToolResult(content)
        self._log(action, outcome, grant_id, reasons, None)
        if self.cfg.get("denial_style", "terminal") == "recoverable":
            msg = recoverable_denial_message(result.reasons[0], action.scope, self.capabilities(action.scope))
        else:
            msg = denial_message(result.reasons[0])
        return ToolResult(msg, is_error=True)

    def _execute(self, action: Action, search_grant: str | None) -> str:
        if action.tool == "email_search":
            kw = {"query": action.args.get("query"), "from_": action.args.get("from"),
                  "unread_only": bool(action.args.get("unread_only"))}
            allowed_senders = self._search_sender_filter(action) if search_grant else None
            rows = []
            for m in self.world.search_candidates(**kw):
                if allowed_senders is not None and not any(
                        principal_matches(m["from"], p) for p in allowed_senders):
                    continue
                rows.append({k: m[k] for k in ("id", "from", "subject", "date", "unread")})
            return json.dumps(rows)
        return json.dumps(self.world.call(action.tool, action.args))

    def _search_sender_filter(self, action: Action) -> list[str] | None:
        """Union of `senders` over every live passing grant; None if any passing grant is unfiltered."""
        assert self.ctx
        union: list[str] = []
        for g in self.grants:
            if g.scope != "email:read" or not evaluate(action, [g], self.world.now, self.ctx).allowed:
                continue
            if g.constraints.senders is None:
                return None
            union.extend(g.constraints.senders)
        return union

    def _log(self, action: Action, outcome: str, grant_id: str | None, reasons: list[str],
             executed_ok: bool | None) -> None:
        assert self.ctx
        self.audit.write(Decision(
            ts=self.world.now, run_id=self.run_id, policy=self.policy_name, task_id=self.ctx.task_id,
            turn=self.turn, tool=action.tool, args=action.args, derived=action.derived, scope=action.scope,
            outcome=outcome, grant_id=grant_id, reasons=reasons, executed_ok=executed_ok))  # type: ignore[arg-type]

    # ---- "what can my agent do right now" ----
    def capabilities(self, scope: str | None = None) -> list[str]:
        """Plain-language live grants, optionally only for one scope."""
        if self.ctx is None:
            raise RuntimeError("begin_turn() must be called first")
        out = []
        for g in self.grants:
            if scope is not None and g.scope != scope:
                continue
            lt = g.lifetime
            if lt.expires_at is not None and self.world.now >= lt.expires_at:
                continue
            if lt.type == "session" and lt.session_id != self.ctx.session_id:
                continue
            if lt.type == "task" and lt.task_id != self.ctx.task_id:
                continue
            if g.max_uses is not None and g.uses >= g.max_uses:
                continue
            out.append(_describe(g))
        return out


def _list(items: list[str]) -> str:
    return ", ".join(items)


def _describe(g: Grant) -> str:
    c = g.constraints
    if g.scope == "email:read":
        what = "Can read email" + (f" from {_list(c.senders)}" if c.senders is not None else "")
        if c.message_ids is not None:
            what += f" (messages {_list(c.message_ids)} only)"
    elif g.scope == "email:send":
        what = "Can send email to " + (_list(c.recipients) if c.recipients is not None else "anyone")
    elif g.scope == "calendar:read":
        what = "Can view your calendar" + _window(c.window)
    elif g.scope == "calendar:write":
        what = "Can create calendar events" + _window(c.window)
        if c.attendees is not None:
            what += " with " + (f"attendees limited to {_list(c.attendees)}" if c.attendees else "no attendees")
    else:
        what = "Can make purchases" + (f" at {_list(c.merchants)}" if c.merchants is not None else " at any merchant")
        if c.max_amount_usd is not None:
            what += f" up to ${c.max_amount_usd:g}"
    notes = []
    if g.max_uses is not None:
        left = g.max_uses - g.uses
        notes.append(f"{left} use{'s' if left != 1 else ''} left")
    notes.append({"task": "expires when this task ends", "session": "expires when this conversation ends",
                  "standing": "no expiry"}[g.lifetime.type])
    src = g.provenance
    if src.source == "user_message" and src.user_text_excerpt:
        ex = src.user_text_excerpt
        notes.append(f"you said: '{ex if len(ex) <= 50 else ex[:50] + '…'}'")
    elif src.source == "user_approval":
        notes.append("you approved a single action")
    elif src.source == "policy_default":
        notes.append("default for this policy")
    return f"{what} ({'; '.join(notes)})"


def _window(w: Any) -> str:
    return f" between {w.start.isoformat()} and {w.end.isoformat()}" if w else ""
