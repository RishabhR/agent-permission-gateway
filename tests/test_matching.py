import pytest

from gateway.matching import (MatchError, matches, normalize, principal_matches,
                              principals_all_match, validate_matcher)
from tests.helpers import SENDERS, act

TZ = "America/Chicago"


def test_principal_exact_and_case_insensitive():
    assert principal_matches("Priya.Shah@Friends.Example", "priya.shah@friends.example")
    assert not principal_matches("dev.k@other.example", "priya.shah@friends.example")


def test_principal_domain_wildcard():
    assert principal_matches("x@acme.example", "@acme.example")
    assert principal_matches("X@ACME.example", "@Acme.Example")
    assert not principal_matches("x@acme-support.example", "@acme.example")
    assert not principal_matches("x@sub.acme.example", "@acme.example")


def test_subset_of_empty_only_true_for_empty_or_missing():
    assert principals_all_match([], [])
    assert not principals_all_match(["a@b.example"], [])


def test_normalize_derives_fields():
    a = act("email_send", to=["A@x.example"], cc=["B@x.example"], subject="s", body="b")
    assert a.scope == "email:send" and a.derived["recipients"] == ["a@x.example", "b@x.example"]
    assert act("email_read", message_id="e01").derived["from"] == "priya.shah@friends.example"
    assert act("calendar_create_event", title="t", start="2026-10-25T16:00:00-05:00",
               end="2026-10-25T18:00:00-05:00").derived["attendees"] == []
    assert act("email_search", query="x").scope == "email:read"


def test_normalize_unknown_tool_raises():
    with pytest.raises(MatchError):
        normalize("shell_exec", {}, SENDERS)


def test_matcher_ops():
    send = act("email_send", to=["jordan.lee@acme.example"], subject="s", body="Friday 24")
    assert matches({"tool": "email_send", "where": {"recipients": {"subset_of": ["@acme.example"]}}}, send, TZ)
    assert matches({"tool": "email_send", "where": {"recipients": {"contains": "JORDAN.lee@acme.example"}}}, send, TZ)
    assert matches({"tool": "email_send", "where": {"body": {"regex": "23|24|25"}}}, send, TZ)
    assert not matches({"tool": "email_read"}, send, TZ)
    assert matches({"tool": "email_send"}, send, TZ)
    buy = act("payments_purchase", merchant="bookshop.example", item="x", amount_usd=34.0)
    assert matches({"tool": "payments_purchase", "where": {"amount_usd": {"lte": 40}, "merchant": {"eq": "bookshop.example"}}}, buy, TZ)
    assert not matches({"tool": "payments_purchase", "where": {"amount_usd": {"lte": 30}}}, buy, TZ)


def test_matcher_date_uses_local_date():
    # 2026-10-26T01:00Z is still Oct 25 in Chicago.
    ev = act("calendar_create_event", title="t", start="2026-10-26T01:00:00+00:00", end="2026-10-26T02:00:00+00:00")
    assert matches({"tool": "calendar_create_event", "where": {"start": {"date": "2026-10-25"}}}, ev, TZ)
    assert not matches({"tool": "calendar_create_event", "where": {"start": {"date": "2026-10-26"}}}, ev, TZ)


def test_matcher_subset_of_empty_attendees():
    ev = act("calendar_create_event", title="t", start="2026-10-24T20:00:00-05:00", end="2026-10-24T22:30:00-05:00")
    m = {"tool": "calendar_create_event", "where": {"attendees": {"subset_of": []}}}
    assert matches(m, ev, TZ)
    ev2 = act("calendar_create_event", title="t", start="2026-10-24T20:00:00-05:00",
              end="2026-10-24T22:30:00-05:00", attendees=["sync@ticketsnow.example"])
    assert not matches(m, ev2, TZ)


def test_unknown_op_and_field_raise():
    a = act("email_read", message_id="e01")
    with pytest.raises(MatchError):
        matches({"tool": "email_read", "where": {"from": {"startswith": "p"}}}, a, TZ)
    with pytest.raises(MatchError):
        matches({"tool": "email_read", "where": {"nonsense": {"eq": 1}}}, a, TZ)
    with pytest.raises(MatchError):
        validate_matcher({"tool": "email_read", "where": {"nonsense": {"eq": 1}}})
    with pytest.raises(MatchError):
        validate_matcher({"tool": "nope"})
