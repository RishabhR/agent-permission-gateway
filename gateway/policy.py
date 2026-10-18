"""Pure policy engine: evaluate(action, grants, now, ctx) -> PolicyResult. No I/O.

Escalation on denial is the gateway's job, not the engine's.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .matching import parse_dt, principal_in
from .models import Action, Constraints, Ctx, Grant, PolicyResult


def _liveness_problem(g: Grant, now: datetime, ctx: Ctx) -> str | None:
    lt = g.lifetime
    if lt.expires_at is not None and now >= lt.expires_at:
        return f"expired at {lt.expires_at.isoformat()}"
    if lt.type == "task" and lt.task_id != ctx.task_id:
        return f"task grant for {lt.task_id}, current task is {ctx.task_id}"
    if lt.type == "session" and lt.session_id != ctx.session_id:
        return f"session grant for {lt.session_id}, current session is {ctx.session_id}"
    if g.max_uses is not None and g.uses >= g.max_uses:
        return f"uses exhausted ({g.uses}/{g.max_uses})"
    return None


def _fmt(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def _check_constraints(g: Grant, action: Action, tz: ZoneInfo) -> list[str]:
    """Return one reason per failed constraint (empty = grant passes)."""
    c: Constraints = g.constraints
    fails: list[str] = []
    p = f"{g.id}: "

    if c.senders is not None and action.tool == "email_read":
        sender = action.field("from")
        if sender is None:
            fails.append(f"{p}sender of message {action.args.get('message_id')!r} unknown, cannot match {_fmt(c.senders)}")
        elif not principal_in(sender, c.senders):
            fails.append(f"{p}sender {sender} not in {_fmt(c.senders)}")
    # email_search + senders: allowed; the gateway filters the results.

    if c.message_ids is not None and action.tool == "email_read":
        mid = action.args.get("message_id")
        if mid not in c.message_ids:
            fails.append(f"{p}message {mid} not in {_fmt(c.message_ids)}")

    if c.recipients is not None:
        for r in action.field("recipients") or []:
            if not principal_in(r, c.recipients):
                fails.append(f"{p}recipient {r} not in {_fmt(c.recipients)}")

    if c.window is not None:
        start, end = (parse_dt(action.args[k], tz) for k in ("start", "end"))
        w = c.window
        if start < w.start or end > w.end:
            fails.append(f"{p}{action.tool} range {start.isoformat()}..{end.isoformat()} "
                         f"outside window {w.start.isoformat()}..{w.end.isoformat()}")

    if c.attendees is not None:
        for a in action.field("attendees") or []:
            if not principal_in(a, c.attendees):
                fails.append(f"{p}attendee {a} not in {_fmt(c.attendees)}")

    if c.merchants is not None:
        m = action.args.get("merchant")
        if m is None or str(m).lower() not in [x.lower() for x in c.merchants]:
            fails.append(f"{p}merchant {m} not in {_fmt(c.merchants)}")

    if c.max_amount_usd is not None:
        amt: Any = action.args.get("amount_usd")
        if amt is None or float(amt) > c.max_amount_usd:
            fails.append(f"{p}amount ${amt} exceeds max_amount_usd ${c.max_amount_usd:g}")
    return fails


def evaluate(action: Action, grants: list[Grant], now: datetime, ctx: Ctx) -> PolicyResult:
    tz = ZoneInfo(ctx.timezone)
    reasons: list[str] = []
    live: list[tuple[int, Grant]] = []
    for idx, g in enumerate(grants):
        if g.scope != action.scope:
            continue
        problem = _liveness_problem(g, now, ctx)
        if problem:
            reasons.append(f"{g.id}: not live: {problem}")
        else:
            live.append((idx, g))

    # Most specific first (most constraints), ties to newest (created_at, then insertion order).
    live.sort(key=lambda t: (len(t[1].constraints.present()), t[1].provenance.created_at, t[0]), reverse=True)

    for _, g in live:
        fails = _check_constraints(g, action, tz)
        if not fails:
            return PolicyResult(allowed=True, grant_id=g.id, reasons=[f"{g.id}: matched"])
        reasons.extend(fails)

    if not live and not reasons:
        reasons.append(f"no grant for scope {action.scope}")
    return PolicyResult(allowed=False, grant_id=None, reasons=reasons)
