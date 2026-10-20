"""Fix B: denial_style recoverable vs terminal."""
import pytest

from gateway.audit import AuditLog
from gateway.gateway import Gateway, denial_message
from gateway.loader import load_policies, validate_manifest
from gateway.world import World

POLICIES = load_policies()
TERMINAL = ("PERMISSION_DENIED: {r}. Do not retry; tell the user what you need permission for.")
BAD_CC = {"to": ["priya.shah@friends.example"], "cc": ["dev.k@other.example"], "subject": "s", "body": "b"}
M = validate_manifest({"grants": [
    {"scope": "email:send", "constraints": {"recipients": ["priya.shah@friends.example"]},
     "user_text_excerpt": "Reply to Priya", "rationale": "r"},
    {"scope": "email:read", "constraints": {"senders": ["priya.shah@friends.example"]},
     "user_text_excerpt": "Reply to Priya", "rationale": "r"}]})


def gw(policy="task_scoped", style=None, approver=lambda a: False):
    cfg = dict(POLICIES[policy])
    if style:
        cfg["denial_style"] = style
    g = Gateway(World(), policy, cfg, approver, AuditLog())
    g.begin_turn("T05", "S", 1, "Reply to Priya with the weekend", M if cfg.get("planner") else None)
    return g


def test_terminal_is_byte_identical_to_current_message():
    reason = "g_t05_t1_m1: recipient dev.k@other.example not in [priya.shah@friends.example]"
    assert denial_message("x") == TERMINAL.format(r="x")
    for style in (None, "terminal"):
        r = gw(style=style).call("email_send", BAD_CC)
        assert r.is_error and r.content.startswith("PERMISSION_DENIED: g_t05_t1_m")
        assert r.content == TERMINAL.format(r=r.content.split("PERMISSION_DENIED: ")[1].split(". Do not retry")[0])
        assert reason.split(":")[1] in r.content or "dev.k@other.example" in r.content


def test_recoverable_format_with_live_grants_for_that_scope_only():
    r = gw(style="recoverable").call("email_send", BAD_CC)
    assert r.is_error
    first = "g_t05_t1_m1: recipient dev.k@other.example not in [priya.shah@friends.example]"
    expected = (f"PERMISSION_DENIED: {first}. Your current permissions for email:send: "
                "Can send email to priya.shah@friends.example (expires when this task ends; you said: "
                "'Reply to Priya'). You may retry with arguments inside these permissions, or tell the user "
                "what additional permission you need.")
    # grant ids are sequence numbers; compare after normalizing the id
    assert r.content.replace("g_t05_t1_m2", "g_t05_t1_m1") == expected
    assert "email:read" not in r.content and "senders" not in r.content  # other scope's grants not shown


def test_recoverable_says_none_when_no_live_grant():
    g = gw("per_action_attentive", "recoverable")
    r = g.call("email_send", BAD_CC)
    assert r.content == ("PERMISSION_DENIED: no grant for scope email:send. Your current permissions for "
                         "email:send: none. You may retry with arguments inside these permissions, or tell "
                         "the user what additional permission you need.")


def test_recoverable_excludes_dead_grants():
    g = gw(style="recoverable")
    g.end_turn()  # task grants expire
    g.begin_turn("T05", "S", 2, "Anything new?", None)
    r = g.call("email_send", BAD_CC)
    assert "permissions for email:send: none." in r.content


def test_message_identical_across_policies_same_state():
    msgs = {p: gw(p, "recoverable").call("email_send", BAD_CC).content
            for p in ("per_action_attentive", "per_action_fatigued")}
    assert len(set(msgs.values())) == 1


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        gw(style="gentle")


def test_capabilities_scope_filter():
    g = gw()
    assert len(g.capabilities()) == 2
    assert len(g.capabilities("email:send")) == 1 and "send email" in g.capabilities("email:send")[0]
    assert g.capabilities("payments:purchase") == []
