"""Deterministic mock tools over eval/fixtures.yaml. In-memory state; reset() per run.

No network, no randomness, no wall clock: `now` comes from the fixtures.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .loader import load_fixtures
from .matching import MatchError, parse_dt

_MSG_PUBLIC = ("id", "from", "to", "cc", "date", "subject", "body")

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "email_search",
        "description": "Search Alex's email. Returns metadata only (no bodies), newest first.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Case-insensitive substring over sender, subject and body."},
            "from": {"type": "string", "description": "Case-insensitive substring of the sender address."},
            "unread_only": {"type": "boolean"}}},
    },
    {
        "name": "email_read",
        "description": "Read the full message and mark it as read.",
        "input_schema": {"type": "object", "properties": {"message_id": {"type": "string"}},
                         "required": ["message_id"]},
    },
    {
        "name": "email_send",
        "description": "Send an email from Alex.",
        "input_schema": {"type": "object", "properties": {
            "to": {"type": "array", "items": {"type": "string"}},
            "cc": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"}, "body": {"type": "string"},
            "in_reply_to": {"type": "string"}},
            "required": ["to", "subject", "body"]},
    },
    {
        "name": "calendar_list",
        "description": "List calendar events overlapping [start, end]. ISO 8601 datetimes.",
        "input_schema": {"type": "object", "properties": {
            "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]},
    },
    {
        "name": "calendar_create_event",
        "description": "Create a calendar event. ISO 8601 datetimes.",
        "input_schema": {"type": "object", "properties": {
            "title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "start", "end"]},
    },
    {
        "name": "payments_purchase",
        "description": "Purchase an item. Merchant, item and price must match the merchant's catalog exactly.",
        "input_schema": {"type": "object", "properties": {
            "merchant": {"type": "string", "description": "Merchant hostname, e.g. shop.example"},
            "item": {"type": "string"}, "amount_usd": {"type": "number"}},
            "required": ["merchant", "item", "amount_usd"]},
    },
]


class World:
    def __init__(self, fixtures: dict[str, Any] | None = None):
        self._fx = fixtures or load_fixtures()
        self.timezone: str = self._fx["clock"]["timezone"]
        self.now: datetime = datetime.fromisoformat(self._fx["clock"]["now"])
        self.reset()

    # ---- lifecycle ----
    def reset(self, task: dict[str, Any] | None = None) -> None:
        """Load fixtures fresh. `task` is accepted for symmetry; arrivals come via deliver_arrivals."""
        fx = copy.deepcopy(self._fx)
        self.inbox: list[dict[str, Any]] = fx["inbox"]
        self.calendar: list[dict[str, Any]] = fx["calendar"]
        self.merchants: dict[str, list[dict[str, Any]]] = fx["merchants"]
        self.contacts: list[dict[str, Any]] = fx["contacts"]
        self._arrivals: dict[str, dict[str, Any]] = {a["id"]: a for a in fx["arrivals"]}
        self.outbox: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.calendar_added: list[dict[str, Any]] = []

    def deliver_arrivals(self, task: dict[str, Any], turn: int) -> list[str]:
        """Inject the task's arrival emails if they are due before this (1-based) turn."""
        spec = task.get("arrivals")
        if not spec or spec["before_turn"] != turn:
            return []
        delivered = []
        for eid in spec["emails"]:
            if eid not in self._arrivals:
                raise KeyError(f"task {task['id']} references unknown arrival {eid}")
            self.inbox.append(self._arrivals[eid])
            delivered.append(eid)
        return delivered

    def senders(self) -> dict[str, str]:
        """message_id -> from address, for normalization."""
        return {m["id"]: m["from"] for m in self.inbox}

    def state(self) -> dict[str, Any]:
        return {"outbox": self.outbox, "orders": self.orders, "calendar_added": self.calendar_added}

    # ---- tools ----
    def call(self, tool: str, args: dict[str, Any]) -> Any:
        fn = getattr(self, f"_{tool}", None)
        if fn is None or tool not in {d["name"] for d in TOOL_DEFS}:
            raise MatchError(f"unknown tool {tool!r}")
        try:
            return fn(**args)
        except TypeError as e:  # missing/unexpected args
            return {"error": f"bad arguments for {tool}: {e}"}

    def _dt(self, s: str) -> datetime:
        return parse_dt(s, self.timezone)

    def search_candidates(self, query: str | None = None, from_: str | None = None,
                          unread_only: bool = False) -> list[dict[str, Any]]:
        out = []
        for m in self.inbox:
            if unread_only and not m["unread"]:
                continue
            if from_ and from_.lower() not in m["from"].lower():
                continue
            if query:
                q = query.lower()
                if not any(q in m.get(k, "").lower() for k in ("from", "subject", "body")):
                    continue
            out.append(m)
        return sorted(out, key=lambda m: self._dt(m["date"]), reverse=True)

    def _email_search(self, query: str | None = None, unread_only: bool = False, **kw: Any) -> Any:
        from_ = kw.pop("from", None)
        if kw:
            raise TypeError(f"unexpected arguments {sorted(kw)}")
        return [{k: m[k] for k in ("id", "from", "subject", "date", "unread")}
                for m in self.search_candidates(query, from_, unread_only)]

    def _email_read(self, message_id: str) -> Any:
        for m in self.inbox:
            if m["id"] == message_id:
                m["unread"] = False
                return {k: m.get(k, []) if k == "cc" else m[k] for k in _MSG_PUBLIC}
        return {"error": f"message {message_id} not found"}

    def _email_send(self, to: list[str], subject: str, body: str,
                    cc: list[str] | None = None, in_reply_to: str | None = None) -> Any:
        if not to:
            return {"error": "'to' must not be empty"}
        msg = {"id": f"sent{len(self.outbox) + 1}", "to": list(to), "cc": list(cc or []),
               "subject": subject, "body": body, "in_reply_to": in_reply_to}
        self.outbox.append(msg)
        return {"status": "sent", "id": msg["id"]}

    def _calendar_list(self, start: str, end: str) -> Any:
        s, e = self._dt(start), self._dt(end)
        evs = [ev for ev in [*self.calendar, *self.calendar_added]
               if self._dt(ev["start"]) < e and self._dt(ev["end"]) > s]
        return sorted(evs, key=lambda ev: self._dt(ev["start"]))

    def _calendar_create_event(self, title: str, start: str, end: str,
                               attendees: list[str] | None = None) -> Any:
        if self._dt(end) <= self._dt(start):
            return {"error": "end must be after start"}
        ev = {"id": f"cn{len(self.calendar_added) + 1}", "title": title, "start": start, "end": end,
              "attendees": list(attendees or [])}
        self.calendar_added.append(ev)
        return {"status": "created", **ev}

    def _payments_purchase(self, merchant: str, item: str, amount_usd: float) -> Any:
        catalog = self.merchants.get(merchant)
        if catalog is None:
            return {"error": f"unknown merchant {merchant}"}
        for entry in catalog:
            if entry["item"] == item and float(entry["amount_usd"]) == float(amount_usd):
                order = {"id": f"ord{len(self.orders) + 1}", "merchant": merchant, "item": item,
                         "amount_usd": float(amount_usd)}
                self.orders.append(order)
                return {"status": "purchased", **order}
        return {"error": f"no catalog item at {merchant} matching item={item!r} amount_usd={amount_usd}"}
