"""Simulated users. Manifest approval is always 'approve as proposed' (handled by the gateway).

attentive: approve an escalation iff the action matches the current turn's `allowed` list.
fatigued:  attentive for the first `fatigue_after` prompts in the task, then approves everything.
"""
from __future__ import annotations

from typing import Any

from gateway.matching import matches
from gateway.models import Action


class UserSim:
    def __init__(self, kind: str, task: dict[str, Any], timezone: str, fatigue_after: int | None = None):
        if kind not in ("attentive", "fatigued"):
            raise ValueError(f"unknown user model {kind!r}")
        if kind == "fatigued" and fatigue_after is None:
            raise ValueError("fatigued user needs fatigue_after")
        self.kind, self.task, self.tz, self.fatigue_after = kind, task, timezone, fatigue_after
        self.turn_idx = 0
        self.prompts_seen = 0

    def set_turn(self, turn: int) -> None:
        """turn is 1-based, matching Gateway turns."""
        self.turn_idx = turn - 1

    def __call__(self, action: Action) -> bool:
        self.prompts_seen += 1
        if self.kind == "fatigued" and self.prompts_seen > self.fatigue_after:  # type: ignore[operator]
            return True
        allowed = self.task["turns"][self.turn_idx].get("allowed", [])
        return any(matches(m, action, self.tz) for m in allowed)
