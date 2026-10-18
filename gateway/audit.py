"""JSONL audit log: exactly one Decision line per tool call; side events kept separately."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Decision


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.decisions: list[Decision] = []
        self.events: list[dict[str, Any]] = []  # e.g. provenance_violation; NOT in the decision file
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, decision: Decision) -> None:
        self.decisions.append(decision)
        if self.path:
            with self.path.open("a") as f:
                f.write(decision.model_dump_json() + "\n")

    def event(self, kind: str, **fields: Any) -> None:
        rec = {"event": kind, **fields}
        self.events.append(rec)
        if self.path:
            with self.path.with_suffix(".events.jsonl").open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
