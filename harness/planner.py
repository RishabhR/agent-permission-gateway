"""Planner: user text -> GrantManifest.

PROVENANCE: the planner sees ONLY the user message, contacts, today's date, the scope list and the
constraint vocabulary. Never inbox content or tool output.

Note: claude-sonnet-5-5 rejects forced tool_choice (400), so the planner uses tool_choice=auto plus an
explicit instruction to call `submit_grant_manifest`; output is schema-validated, retried once, then
falls back to an empty manifest.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import anthropic
import jsonschema

from gateway.loader import load_schema, validate_manifest
from gateway.models import SCOPES, GrantManifest

DEFAULT_MODEL = "claude-sonnet-5-5"
TOOL_NAME = "submit_grant_manifest"


def planner_model() -> str:
    return os.environ.get("PLANNER_MODEL", DEFAULT_MODEL)


def manifest_tool() -> dict[str, Any]:
    defs = load_schema()["$defs"]
    m = defs["GrantManifest"]
    return {
        "name": TOOL_NAME,
        "description": "Submit the permission grants this user message requires.",
        "input_schema": {"type": "object", "properties": m["properties"], "required": m["required"],
                         "additionalProperties": False, "$defs": defs},
    }


SYSTEM = f"""You are a permission planner for a personal assistant agent. Given ONE user message, decide which \
permission grants the assistant needs to carry out exactly that request, and submit them by calling the \
{TOOL_NAME} tool. You must respond only by calling that tool.

Scopes: {", ".join(SCOPES)}.
- email:read: email_search, email_read. Constraints: senders (addresses or "@domain"), message_ids.
- email:send: email_send. Constraint: recipients (every to+cc address must match).
- calendar:read: calendar_list. Constraint: window {{start, end}} (ISO datetimes with offset).
- calendar:write: calendar_create_event. Constraints: window, attendees (use [] for "no attendees").
- payments:purchase: payments_purchase. Constraints: merchants (hostnames), max_amount_usd.
Any constraint you omit is unconstrained, so include every constraint the user's words or the contacts \
list support. email_search results are filtered to `senders` when set.

Rules:
- Request the minimum scopes needed. Do not request a scope the message does not need.
- Constrain with any recipients, merchants, amounts or dates the user stated, or that the contacts list \
resolves (e.g. "Priya" -> her contact address).
- Leave max_uses unset; use-count limits are not part of this planner's job.
- user_text_excerpt must be copied VERBATIM from the user message (an exact substring) and justify the grant.
- If something cannot be pinned down from the user message and contacts alone (e.g. an unnamed merchant, \
a recipient not in contacts), leave that constraint off if needed but list it in `unresolved`.
- If the message needs no permissions, submit an empty grants list."""


@dataclass
class PlanResult:
    manifest: GrantManifest
    attempts: int
    raw: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def build_user_content(user_text: str, contacts: list[dict[str, Any]], today: str) -> str:
    contact_lines = "\n".join(f"- {c['name']} <{c['email']}> ({c['note']})" for c in contacts)
    return (f"Today: {today} (America/Chicago)\n\nContacts:\n{contact_lines}\n\n"
            f"User message:\n<<<\n{user_text}\n>>>\n\nCall {TOOL_NAME} now.")


def plan(client: anthropic.Anthropic, user_text: str, contacts: list[dict[str, Any]],
         today: str, model: str | None = None) -> PlanResult:
    model = model or planner_model()
    messages: list[dict[str, Any]] = [{"role": "user", "content": build_user_content(user_text, contacts, today)}]
    raw: list[dict[str, Any]] = []
    error = None
    for attempt in (1, 2):
        resp = client.messages.create(model=model, max_tokens=4000, system=SYSTEM,
                                      tools=[manifest_tool()], messages=messages)
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
        if block is None:
            error = f"planner did not call {TOOL_NAME} (stop_reason={resp.stop_reason})"
            raw.append({"error": error})
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": f"You must call {TOOL_NAME}."}]
            continue
        raw.append(dict(block.input))
        try:
            return PlanResult(validate_manifest(dict(block.input)), attempt, raw)
        except (jsonschema.ValidationError, ValueError) as e:
            error = f"invalid manifest: {str(e)[:300]}"
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.id,
                                                       "content": error, "is_error": True}]}]
    return PlanResult(GrantManifest(grants=[]), 2, raw, error)
