"""Principal + matcher ops, shared by the policy engine AND the scorer.

Unknown ops, fields or tools raise MatchError; nothing is silently treated as a match.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .models import TOOL_SCOPE, Action

TOOL_FIELDS: dict[str, frozenset[str]] = {
    "email_search": frozenset({"query", "from", "unread_only"}),
    "email_read": frozenset({"message_id", "from"}),
    "email_send": frozenset({"to", "cc", "subject", "body", "in_reply_to", "recipients"}),
    "calendar_list": frozenset({"start", "end"}),
    "calendar_create_event": frozenset({"title", "start", "end", "attendees"}),
    "payments_purchase": frozenset({"merchant", "item", "amount_usd"}),
}
OPS = frozenset({"eq", "in", "subset_of", "contains", "date", "lte", "regex"})


class MatchError(ValueError):
    """Bad matcher, unknown tool/field/op, or unparseable value."""


# ---------- principals ----------
def principal_matches(address: str, principal: str) -> bool:
    """Case-insensitive; '@domain' matches any address at exactly that domain."""
    address, principal = address.strip().lower(), principal.strip().lower()
    if principal.startswith("@"):
        return address.endswith(principal) and "@" in address and address.rsplit("@", 1)[1] == principal[1:]
    return address == principal


def principal_in(address: str, principals: list[str]) -> bool:
    return any(principal_matches(address, p) for p in principals)


def principals_all_match(addresses: list[str] | None, principals: list[str]) -> bool:
    """subset_of semantics: every address matches some principal. `[]` allows only empty/missing."""
    return all(principal_in(a, principals) for a in (addresses or []))


# ---------- time ----------
def parse_dt(value: Any, tz: ZoneInfo | str) -> datetime:
    """Parse an ISO datetime; naive values are read in the fixture timezone."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError as e:
            raise MatchError(f"unparseable datetime {value!r}") from e
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return dt.replace(tzinfo=zone) if dt.tzinfo is None else dt


def local_date(value: Any, tz: ZoneInfo | str) -> str:
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return parse_dt(value, zone).astimezone(zone).date().isoformat()


# ---------- normalization ----------
def normalize(tool: str, args: Mapping[str, Any], senders: Mapping[str, str]) -> Action:
    """Turn a tool call into an Action. `senders` maps message_id -> from address."""
    if tool not in TOOL_SCOPE:
        raise MatchError(f"unknown tool {tool!r}")
    derived: dict[str, Any] = {}
    if tool == "email_read":
        mid = args.get("message_id")
        derived["from"] = senders[mid].lower() if mid in senders else None
    elif tool == "email_send":
        derived["recipients"] = [a.lower() for a in [*(args.get("to") or []), *(args.get("cc") or [])]]
    elif tool == "calendar_create_event":
        derived["attendees"] = [a.lower() for a in (args.get("attendees") or [])]
    return Action(tool=tool, scope=TOOL_SCOPE[tool], args=dict(args), derived=derived)  # type: ignore[arg-type]


# ---------- matcher (tasks.yaml) ----------
def validate_matcher(matcher: Mapping[str, Any]) -> None:
    tool = matcher.get("tool")
    if tool not in TOOL_FIELDS:
        raise MatchError(f"matcher has unknown tool {tool!r}")
    extra = set(matcher) - {"tool", "where"}
    if extra:
        raise MatchError(f"matcher has unknown keys {sorted(extra)}")
    for field, cond in (matcher.get("where") or {}).items():
        if field not in TOOL_FIELDS[tool]:
            raise MatchError(f"unknown field {field!r} for tool {tool!r}")
        if not isinstance(cond, Mapping) or len(cond) != 1:
            raise MatchError(f"condition for {field!r} must be a single {{op: value}}: {cond!r}")
        (op,) = cond
        if op not in OPS:
            raise MatchError(f"unknown op {op!r} for field {field!r}")


def _op(op: str, actual: Any, expected: Any, tz: str) -> bool:
    if op == "subset_of":  # missing/empty list is a valid subset
        return principals_all_match(_as_list(actual), list(expected))
    if actual is None:
        return False
    if op == "eq":
        return _norm(actual) == _norm(expected)
    if op == "in":
        return principal_in(str(actual), list(expected))
    if op == "contains":
        if isinstance(actual, (list, tuple)):
            return any(_norm(x) == _norm(expected) for x in actual)
        return str(expected).lower() in str(actual).lower()
    if op == "date":
        return local_date(actual, tz) == str(expected)
    if op == "lte":
        return float(actual) <= float(expected)
    if op == "regex":
        return re.search(str(expected), str(actual)) is not None
    raise MatchError(f"unknown op {op!r}")


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def matches(matcher: Mapping[str, Any], action: Action, tz: str) -> bool:
    """True iff the action's tool equals matcher.tool and every `where` condition holds."""
    validate_matcher(matcher)
    if action.tool != matcher["tool"]:
        return False
    for field, cond in (matcher.get("where") or {}).items():
        (op, expected), = cond.items()
        if not _op(op, action.field(field), expected, tz):
            return False
    return True
