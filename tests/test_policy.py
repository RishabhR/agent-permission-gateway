import pytest

from gateway.matching import MatchError
from gateway.models import Ctx
from gateway.policy import evaluate
from tests.helpers import CTX, NOW, act, grant

SEND = dict(subject="s", body="b")


def ev(action, grants, ctx=CTX):
    return evaluate(action, grants, NOW, ctx)


# ---- principals ----
def test_domain_wildcard_match_and_nonmatch():
    g = [grant("email:send", {"recipients": ["@acme.example"]})]
    assert ev(act("email_send", to=["jordan.lee@acme.example"], **SEND), g).allowed
    r = ev(act("email_send", to=["verify@acme-support.example"], **SEND), g)
    assert not r.allowed and r.reasons


def test_case_insensitive_recipients():
    g = [grant("email:send", {"recipients": ["Priya.Shah@Friends.Example"]})]
    assert ev(act("email_send", to=["PRIYA.SHAH@friends.example"], **SEND), g).allowed


def test_all_to_and_cc_must_match_stray_cc_fails():
    g = [grant("email:send", {"recipients": ["priya.shah@friends.example"]}, gid="g_003")]
    assert ev(act("email_send", to=["priya.shah@friends.example"], **SEND), g).allowed
    r = ev(act("email_send", to=["priya.shah@friends.example"], cc=["dev.k@other.example"], **SEND), g)
    assert not r.allowed
    assert r.reasons == ["g_003: recipient dev.k@other.example not in [priya.shah@friends.example]"]


# ---- purchase ----
def test_purchase_merchant_and_amount_cap():
    g = [grant("payments:purchase", {"merchants": ["bookshop.example"], "max_amount_usd": 40})]
    assert ev(act("payments_purchase", merchant="bookshop.example", item="b", amount_usd=34), g).allowed
    over = ev(act("payments_purchase", merchant="bookshop.example", item="b", amount_usd=249), g)
    assert not over.allowed and "exceeds" in over.reasons[0]
    wrong = ev(act("payments_purchase", merchant="dealsdaily.example", item="b", amount_usd=10), g)
    assert not wrong.allowed and "dealsdaily.example" in wrong.reasons[0]


def test_max_uses_one_fails_second_use():
    g = grant("payments:purchase", {"merchants": ["bookshop.example"]}, max_uses=1)
    a = act("payments_purchase", merchant="bookshop.example", item="b", amount_usd=34)
    assert ev(a, [g]).allowed
    g.uses = 1
    r = ev(a, [g])
    assert not r.allowed and "uses" in r.reasons[0]


# ---- calendar ----
WIN = {"start": "2026-10-24T00:00:00-05:00", "end": "2026-10-24T23:59:00-05:00"}


def test_calendar_window_event_ending_outside_fails():
    g = [grant("calendar:write", {"window": WIN})]
    ok = act("calendar_create_event", title="t", start="2026-10-24T20:00:00-05:00", end="2026-10-24T22:30:00-05:00")
    late = act("calendar_create_event", title="t", start="2026-10-24T23:00:00-05:00", end="2026-10-25T00:30:00-05:00")
    assert ev(ok, g).allowed
    r = ev(late, g)
    assert not r.allowed and r.reasons


def test_calendar_list_range_inside_window():
    g = [grant("calendar:read", {"window": WIN})]
    assert ev(act("calendar_list", start="2026-10-24T08:00:00-05:00", end="2026-10-24T18:00:00-05:00"), g).allowed
    assert not ev(act("calendar_list", start="2026-10-24T08:00:00-05:00", end="2026-10-26T18:00:00-05:00"), g).allowed


def test_naive_datetimes_read_in_fixture_timezone():
    g = [grant("calendar:read", {"window": WIN})]
    assert ev(act("calendar_list", start="2026-10-24T08:00:00", end="2026-10-24T18:00:00"), g).allowed


def test_empty_attendees_constraint():
    g = [grant("calendar:write", {"attendees": []})]
    bare = act("calendar_create_event", title="t", start="2026-10-24T20:00:00-05:00", end="2026-10-24T22:30:00-05:00")
    inv = act("calendar_create_event", title="t", start="2026-10-24T20:00:00-05:00",
              end="2026-10-24T22:30:00-05:00", attendees=["sync@ticketsnow.example"])
    assert ev(bare, g).allowed
    r = ev(inv, g)
    assert not r.allowed and "sync@ticketsnow.example" in r.reasons[0]


# ---- lifetimes ----
def test_task_grant_not_live_in_other_task():
    g = [grant("payments:purchase", lifetime={"type": "task", "task_id": "T15"}, gid="g_010")]
    a = act("payments_purchase", merchant="bookshop.example", item="b", amount_usd=34)
    assert ev(a, g, Ctx(task_id="T15", session_id="S1")).allowed
    r = ev(a, g, Ctx(task_id="T16", session_id="S1"))
    assert not r.allowed and "g_010" in r.reasons[0] and "T15" in r.reasons[0]


def test_session_grant_live_across_tasks_same_session_only():
    g = [grant("payments:purchase", lifetime={"type": "session", "session_id": "S1"})]
    a = act("payments_purchase", merchant="bookshop.example", item="b", amount_usd=34)
    assert ev(a, g, Ctx(task_id="T15", session_id="S1")).allowed
    assert ev(a, g, Ctx(task_id="T16", session_id="S1")).allowed
    assert not ev(a, g, Ctx(task_id="T16", session_id="S2")).allowed


def test_expired_grant_not_live():
    g = [grant("email:read", lifetime={"type": "standing", "expires_at": "2026-10-19T08:00:00-05:00"})]
    assert not ev(act("email_search", query="x"), g).allowed


# ---- unconstrained, specificity, reasons ----
def test_standing_grant_without_constraints_allows_anything_in_scope():
    g = [grant("email:send")]
    assert ev(act("email_send", to=["anyone@evil.example"], cc=["x@y.example"], **SEND), g).allowed
    assert not ev(act("calendar_list", start="2026-10-24", end="2026-10-25"), g).allowed  # wrong scope


def test_most_specific_grant_attributed():
    broad = grant("email:send", gid="g_broad")
    narrow = grant("email:send", {"recipients": ["priya.shah@friends.example"]}, gid="g_narrow")
    r = ev(act("email_send", to=["priya.shah@friends.example"], **SEND), [narrow, broad])
    assert r.allowed and r.grant_id == "g_narrow"
    r = ev(act("email_send", to=["priya.shah@friends.example"], **SEND), [broad, narrow])
    assert r.grant_id == "g_narrow"


def test_ties_go_to_newest():
    old = grant("email:send", {"recipients": ["@a.example"]}, gid="g_old", created_at="2026-10-19T08:00:00-05:00")
    new = grant("email:send", {"recipients": ["@a.example"]}, gid="g_new", created_at="2026-10-19T08:30:00-05:00")
    assert ev(act("email_send", to=["x@a.example"], **SEND), [new, old]).grant_id == "g_new"
    assert ev(act("email_send", to=["x@a.example"], **SEND), [old, new]).grant_id == "g_new"


def test_falls_through_to_less_specific_grant_when_specific_fails():
    narrow = grant("email:send", {"recipients": ["priya.shah@friends.example"]}, gid="g_narrow")
    broad = grant("email:send", {"recipients": ["@friends.example"]}, gid="g_broad")
    r = ev(act("email_send", to=["x@friends.example"], **SEND), [narrow, broad])
    assert r.allowed and r.grant_id == "g_broad"


def test_reasons_nonempty_on_every_denial():
    a = act("email_send", to=["x@evil.example"], **SEND)
    for grants in ([], [grant("email:read")], [grant("email:send", {"recipients": ["@a.example"]})],
                   [grant("email:send", max_uses=1, uses=1)]):
        r = ev(a, grants)
        assert not r.allowed and r.grant_id is None and r.reasons and all(r.reasons)


# ---- reads ----
def test_email_read_senders_and_message_ids():
    g = [grant("email:read", {"senders": ["@friends.example"]})]
    assert ev(act("email_read", message_id="e01"), g).allowed
    assert not ev(act("email_read", message_id="e07"), g).allowed
    g2 = [grant("email:read", {"message_ids": ["e07"]})]
    assert ev(act("email_read", message_id="e07"), g2).allowed
    assert not ev(act("email_read", message_id="e01"), g2).allowed


def test_email_search_with_senders_constraint_still_allowed():
    g = [grant("email:read", {"senders": ["@friends.example"]})]
    assert ev(act("email_search", query="anything"), g).allowed


def test_unknown_message_id_denied_with_reason():
    g = [grant("email:read", {"senders": ["@friends.example"]})]
    r = ev(act("email_read", message_id="zzz"), g)
    assert not r.allowed and "zzz" in r.reasons[0]


def test_engine_is_pure_does_not_mutate_grants():
    g = grant("email:send", max_uses=2)
    before = g.model_dump()
    ev(act("email_send", to=["a@b.example"], **SEND), [g])
    assert g.model_dump() == before


def test_invalid_constraint_for_scope_rejected():
    with pytest.raises(ValueError):
        grant("email:read", {"recipients": ["a@b.example"]})
