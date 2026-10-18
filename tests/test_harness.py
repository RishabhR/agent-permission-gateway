"""Offline tests of agent loop, planner and user sim using a scripted fake Anthropic client."""
import json
from types import SimpleNamespace

from anthropic.types import TextBlock, ToolUseBlock

from gateway.audit import AuditLog
from gateway.gateway import Gateway
from gateway.loader import load_fixtures, load_policies, load_tasks
from gateway.world import World
from harness import agent as agent_mod
from harness.agent import Agent
from harness.planner import SYSTEM, TOOL_NAME, build_user_content, manifest_tool, plan
from harness.session import run_task
from harness.user_sim import UserSim

POLICIES = load_policies()
TASKS = {t["id"]: t for t in load_tasks()}


def tu(i, name, **inp):
    return ToolUseBlock(id=f"tu{i}", name=name, input=inp, type="tool_use")


def resp(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def text(t):
    return TextBlock(type="text", text=t, citations=None)


class Fake:
    """Scripted responses, popped in order; records requests."""
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.messages = self

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


def test_agent_loop_runs_tools_through_gateway_and_persists_history():
    world, audit = World(), AuditLog()
    gw = Gateway(world, "standing", POLICIES["standing"], lambda a: True, audit)
    gw.begin_turn("T01", "S", 1, "q")
    fake = Fake([resp(tu(1, "email_search", query="dental"), tu(2, "email_read", message_id="e12")),
                 resp(text("Your cleaning is Oct 28."), stop="end_turn")])
    a = Agent(fake, gw, "m")
    r = a.run_turn("When's my dentist?")
    assert r.final_reply == "Your cleaning is Oct 28." and r.tool_calls == 2
    assert len(audit.decisions) == 2
    # both results returned in ONE user message
    last_user = fake.requests[1]["messages"][-1]
    assert last_user["role"] == "user" and len(last_user["content"]) == 2
    assert fake.requests[0]["system"] == agent_mod.SYSTEM_PROMPT and fake.requests[0]["tools"]
    # history persists into a second turn
    fake.script += [resp(text("ok"), stop="end_turn")]
    a.run_turn("thanks")
    assert len(fake.requests[2]["messages"]) == 5


def test_agent_tool_call_cap():
    world, audit = World(), AuditLog()
    gw = Gateway(world, "standing", POLICIES["standing"], lambda a: True, audit)
    gw.begin_turn("T01", "S", 1, "q")
    batch = [tu(i, "email_search", query="x") for i in range(10)]
    fake = Fake([resp(*batch), resp(*[tu(100 + i, "email_search") for i in range(10)]),
                 resp(text("done"), stop="end_turn")])
    r = Agent(fake, gw, "m").run_turn("go")
    assert r.tool_calls == 15 and r.hit_call_cap and len(audit.decisions) == 15 and r.final_reply == "done"


def test_denied_call_marked_is_error():
    world, audit = World(), AuditLog()
    gw = Gateway(world, "per_action_attentive", POLICIES["per_action_attentive"], lambda a: False, audit)
    gw.begin_turn("T01", "S", 1, "q")
    fake = Fake([resp(tu(1, "email_send", to=["a@b.example"], subject="s", body="b")),
                 resp(text("denied"), stop="end_turn")])
    Agent(fake, gw, "m").run_turn("send")
    tr = fake.requests[1]["messages"][-1]["content"][0]
    assert tr["is_error"] and tr["content"].startswith("PERMISSION_DENIED")


def test_planner_prompt_contains_only_user_text_contacts_date():
    fx = load_fixtures()
    content = build_user_content("Reply to Priya", fx["contacts"], "2026-10-19")
    assert "Reply to Priya" in content and "priya.shah@friends.example" in content
    for e in fx["inbox"]:
        assert e["subject"] not in content and e["body"].strip() not in content


def test_manifest_tool_schema_shape():
    t = manifest_tool()
    assert t["name"] == TOOL_NAME and t["input_schema"]["required"] == ["grants"]
    assert "ProposedGrant" in t["input_schema"]["$defs"]


GOOD = {"grants": [{"scope": "email:send", "constraints": {"recipients": ["priya.shah@friends.example"]},
                    "user_text_excerpt": "Reply to Priya", "rationale": "reply"}], "unresolved": []}


def test_planner_success_retry_and_empty_fallback():
    contacts = load_fixtures()["contacts"]
    fake = Fake([resp(tu(1, TOOL_NAME, **GOOD))])
    r = plan(fake, "Reply to Priya", contacts, "2026-10-19", "m")
    assert r.attempts == 1 and r.manifest.grants[0].scope == "email:send" and r.error is None
    assert "tool_choice" not in fake.requests[0]  # forced tool_choice is a 400 on sonnet 5.5
    bad = {"grants": [{"scope": "email:delete", "user_text_excerpt": "x", "rationale": "y"}]}
    fake = Fake([resp(tu(1, TOOL_NAME, **bad)), resp(tu(2, TOOL_NAME, **GOOD))])
    r = plan(fake, "Reply to Priya", contacts, "2026-10-19", "m")
    assert r.attempts == 2 and len(r.manifest.grants) == 1
    fake = Fake([resp(tu(1, TOOL_NAME, **bad)), resp(text("no"), stop="end_turn")])
    r = plan(fake, "Reply to Priya", contacts, "2026-10-19", "m")
    assert r.manifest.grants == [] and r.error


def test_user_sim_attentive_and_fatigued():
    task = TASKS["T05"]
    ok = Gateway(World(), "standing", POLICIES["standing"], lambda a: True, AuditLog())
    ok.begin_turn("T05", "S", 1, "x")
    from gateway.matching import normalize
    good = normalize("email_send", {"to": ["priya.shah@friends.example"], "subject": "s", "body": "b"}, {})
    bad = normalize("email_send", {"to": ["priya.shah@friends.example"], "cc": ["dev.k@other.example"],
                                   "subject": "s", "body": "b"}, {})
    att = UserSim("attentive", task, "America/Chicago")
    assert att(good) and not att(bad)
    fat = UserSim("fatigued", task, "America/Chicago", fatigue_after=2)
    assert [fat(good), fat(bad), fat(bad), fat(bad)] == [True, False, True, True]


def test_run_task_end_to_end_with_fake_client_task_scoped():
    task = TASKS["T04"]
    manifest = {"grants": [
        {"scope": "email:read", "constraints": {"senders": ["linda.rivera@family.example"]},
         "user_text_excerpt": "Mom suggested", "rationale": "r"},
        {"scope": "payments:purchase", "constraints": {"merchants": ["bookshop.example"], "max_amount_usd": 40},
         "user_text_excerpt": "under $40", "rationale": "r"}], "unresolved": []}
    fake = Fake([
        resp(tu(1, TOOL_NAME, **manifest)),                                   # planner
        resp(tu(2, "email_search", query="gift"), tu(3, "email_read", message_id="e09")),
        resp(tu(4, "payments_purchase", merchant="bookshop.example",
                item="The Lighthouse Keeper's Ledger", amount_usd=34)),
        resp(text("Ordered the book for $34."), stop="end_turn")])
    rec = run_task(fake, "task_scoped", task, agent_model="m", planner_model="m")
    t = rec["turns"][0]
    assert t["prompts"] == 1 and [d["outcome"] for d in t["decisions"]] == ["allowed"] * 3
    assert rec["world_after"]["orders"][0]["amount_usd"] == 34.0
    assert t["final_reply"].startswith("Ordered")
