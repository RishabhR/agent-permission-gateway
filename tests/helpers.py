from __future__ import annotations

from datetime import datetime
from itertools import count

from gateway.matching import normalize
from gateway.models import Action, Ctx, Grant

NOW = datetime.fromisoformat("2026-10-19T09:00:00-05:00")
CTX = Ctx(task_id="T01", session_id="S1")
SENDERS = {"e01": "priya.shah@friends.example", "e07": "alerts@firstlake.example"}

_ids = count(1)


def grant(scope: str, constraints: dict | None = None, lifetime: dict | None = None,
          max_uses: int | None = None, uses: int = 0, created_at: str = "2026-10-19T09:00:00-05:00",
          gid: str | None = None) -> Grant:
    return Grant.model_validate({
        "id": gid or f"g_{next(_ids):03d}",
        "scope": scope,
        "constraints": constraints or {},
        "lifetime": lifetime or {"type": "standing"},
        "max_uses": max_uses,
        "uses": uses,
        "provenance": {"source": "user_message", "created_at": created_at},
    })


def act(tool: str, **args) -> Action:
    return normalize(tool, args, SENDERS)
