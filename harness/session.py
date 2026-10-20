"""Run one task (all turns) under one policy and return a run record (see PLAN M5)."""
from __future__ import annotations

import sys
from typing import Any

import anthropic

from gateway.audit import AuditLog
from gateway.gateway import Gateway
from gateway.loader import load_fixtures, load_policies, load_tasks
from gateway.models import GrantManifest
from gateway.world import World
from harness.agent import Agent
from harness.planner import plan
from harness.user_sim import UserSim


def run_task(client: anthropic.Anthropic, policy_name: str, task: dict[str, Any], repeat: int = 0,
             world: World | None = None, policies: dict[str, Any] | None = None,
             agent_model: str | None = None, planner_model: str | None = None) -> dict[str, Any]:
    policies = policies or load_policies()
    cfg = policies[policy_name]
    world = world or World()
    world.reset(task)
    run_id = f"{policy_name}:{task['id']}:{repeat}"
    audit = AuditLog()
    sim = UserSim(cfg["user_model"], task, world.timezone, cfg.get("fatigue_after"))
    gw = Gateway(world, policy_name, cfg, sim, audit, run_id=run_id)
    agent = Agent(client, gw, agent_model)
    contacts = load_fixtures()["contacts"]
    session_id = f"s_{run_id}"
    turns = []
    for i, t in enumerate(task["turns"], start=1):
        world.deliver_arrivals(task, i)
        sim.set_turn(i)
        manifest: GrantManifest | None = None
        planner_info: dict[str, Any] = {}
        if cfg.get("planner"):
            pr = plan(client, t["user"], contacts, "2026-10-19", planner_model)
            manifest = pr.manifest
            planner_info = {"attempts": pr.attempts, "error": pr.error}
        start = gw.begin_turn(task["id"], session_id, i, t["user"], manifest)
        n_before = len(audit.decisions)
        res = agent.run_turn(t["user"])
        gw.end_turn()
        turns.append({
            "user": t["user"],
            "manifest": manifest.model_dump(mode="json") if manifest else None,
            "planner": planner_info,
            "minted": start.minted, "provenance_dropped": start.dropped,
            "deferred_unkeyed": start.deferred, "minted_grants": start.minted_grants,
            "decisions": [d.model_dump(mode="json") for d in audit.decisions[n_before:]],
            "final_reply": res.final_reply, "stop_reason": res.stop_reason, "hit_call_cap": res.hit_call_cap,
            "prompts": gw.turn_prompts,
        })
    flags = {"defer_unkeyed_writes": cfg.get("defer_unkeyed_writes", False),
             "denial_style": cfg.get("denial_style", "terminal")}
    return {"run_id": run_id, "policy": policy_name, "task_id": task["id"], "repeat": repeat, "flags": flags,
            "turns": turns, "world_after": world.state(), "audit_events": audit.events}


if __name__ == "__main__":  # python -m harness.session <policy> <task_id>
    from harness.env import load_env
    load_env()
    pol, tid = sys.argv[1], sys.argv[2]
    task = next(t for t in load_tasks() if t["id"] == tid)
    import json
    print(json.dumps(run_task(anthropic.Anthropic(max_retries=6), pol, task), indent=2))
