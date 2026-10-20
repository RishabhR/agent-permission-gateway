"""Fix A: defer_unkeyed_writes drops write grants missing their key constraint before minting."""
import json

from gateway.audit import AuditLog
from gateway.gateway import Gateway
from gateway.loader import load_policies, validate_manifest
from gateway.world import World

POLICIES = load_policies()
TEXT = "Reply all on the Door County thread: I'm in for Oct 23-25."


def gw_for(policy="task_scoped", flag=False, approver=lambda a: True):
    cfg = {**POLICIES[policy], "defer_unkeyed_writes": flag}
    return Gateway(World(), policy, cfg, approver, AuditLog())


def manifest(*grants, unresolved=()):
    return validate_manifest({"grants": [{"user_text_excerpt": "Door County thread", "rationale": "r", **g}
                                         for g in grants], "unresolved": list(unresolved)})


SEND_UNKEYED = {"scope": "email:send"}
SEND_KEYED = {"scope": "email:send", "constraints": {"recipients": ["priya.shah@friends.example"]}}
READ_UNKEYED = {"scope": "email:read"}
BUY_UNKEYED = {"scope": "payments:purchase", "constraints": {"max_amount_usd": 40}}
CAL_EMPTY_ATT = {"scope": "calendar:write", "constraints": {"attendees": []}}
CAL_UNKEYED = {"scope": "calendar:write"}
SEND_CALL = {"to": ["dev.k@other.example"], "subject": "s", "body": "b"}


def test_unkeyed_write_dropped_logged_and_escalates_on_call():
    approved = []
    g = gw_for(flag=True, approver=lambda a: approved.append(a) or True)
    start = g.begin_turn("T16", "S", 1, TEXT, manifest(SEND_UNKEYED, READ_UNKEYED, unresolved=["recipients unknown"]))
    assert start.deferred == ["email:send"] and len(start.minted) == 1  # read grant still minted
    assert [x["scope"] for x in start.minted_grants] == ["email:read"]
    ev = g.audit.events[0]
    assert ev["event"] == "deferred_unkeyed" and ev["missing"] == "recipients"
    assert ev["rationale"] == "r" and ev["unresolved"] == ["recipients unknown"]
    r = g.call("email_send", SEND_CALL)
    assert len(approved) == 1 and not r.is_error  # escalation path ran, user approved
    d = g.audit.decisions[0]
    assert d.outcome == "escalated_approved" and d.reasons[0] == "no grant for scope email:send"
    assert g.task_prompts == 2  # manifest + the escalation


def test_all_three_write_scopes_covered_and_empty_attendees_counts_as_keyed():
    g = gw_for(flag=True)
    start = g.begin_turn("T1", "S", 1, TEXT, manifest(SEND_UNKEYED, BUY_UNKEYED, CAL_UNKEYED, CAL_EMPTY_ATT))
    assert sorted(start.deferred) == ["calendar:write", "email:send", "payments:purchase"]
    assert [x["scope"] for x in start.minted_grants] == ["calendar:write"]
    assert start.minted_grants[0]["constraints"] == {"attendees": []}


def test_keyed_grant_unaffected():
    g = gw_for(flag=True)
    start = g.begin_turn("T05", "S", 1, TEXT, manifest(SEND_KEYED))
    assert start.deferred == [] and len(start.minted) == 1 and g.audit.events == []
    assert not g.call("email_send", {"to": ["priya.shah@friends.example"], "subject": "s", "body": "b"}).is_error
    assert g.audit.decisions[0].outcome == "allowed" and g.task_prompts == 1


def test_all_grants_dropped_means_no_manifest_prompt():
    g = gw_for(flag=True)
    assert g.begin_turn("T1", "S", 1, TEXT, manifest(SEND_UNKEYED)).prompts == 0


def test_flag_off_reproduces_baseline_exactly():
    m = manifest(SEND_UNKEYED, READ_UNKEYED)
    off, base = gw_for(flag=False), Gateway(World(), "task_scoped", POLICIES["task_scoped"], lambda a: True, AuditLog())
    s_off, s_base = off.begin_turn("T16", "S", 1, TEXT, m), base.begin_turn("T16", "S", 1, TEXT, m)
    assert s_off.minted == s_base.minted and s_off.deferred == [] and s_off.prompts == s_base.prompts == 1
    assert [g.model_dump() for g in off.grants] == [g.model_dump() for g in base.grants]
    assert off.audit.events == base.audit.events == []
    for gw in (off, base):
        gw.call("email_send", SEND_CALL)
    assert off.audit.decisions[0].outcome == base.audit.decisions[0].outcome == "allowed"  # unkeyed grant allows anything


def test_flag_ignored_by_non_planner_policies():
    g = gw_for("per_action_attentive", flag=True)
    g.begin_turn("T1", "S", 1, TEXT, manifest(SEND_UNKEYED))
    assert g.audit.events == [] and g.task_prompts == 0


def test_provenance_check_still_runs_first():
    g = gw_for(flag=True)
    bad = validate_manifest({"grants": [{"scope": "email:send", "user_text_excerpt": "not in text", "rationale": "r"}]})
    start = g.begin_turn("T1", "S", 1, TEXT, bad)
    assert start.dropped == ["not in text"] and start.deferred == []
    assert g.audit.events[0]["event"] == "provenance_violation"
