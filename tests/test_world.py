from gateway.loader import load_tasks
from gateway.world import World


def mk():
    return World()


def task(tid):
    return next(t for t in load_tasks() if t["id"] == tid)


def test_search_metadata_only_newest_first():
    r = mk().call("email_search", {})
    assert all(set(m) == {"id", "from", "subject", "date", "unread"} for m in r)
    assert r[0]["id"] == "e06"  # newest: Oct 18 22:15
    assert [m["id"] for m in r][-1] == "e02"  # oldest: Oct 2


def test_search_filters():
    w = mk()
    assert [m["id"] for m in w.call("email_search", {"query": "DENTAL"})] == ["e12"]
    assert {m["id"] for m in w.call("email_search", {"from": "Priya"})} == {"e01", "e15"}
    assert all(m["unread"] for m in w.call("email_search", {"unread_only": True}))
    assert w.call("email_search", {"query": "water"})[0]["id"] == "e02"  # body match


def test_read_returns_body_and_marks_read():
    w = mk()
    assert any(m["id"] == "e04" for m in w.call("email_search", {"unread_only": True}))
    m = w.call("email_read", {"message_id": "e04"})
    assert "Friday at 2pm" in m["body"] and m["from"] == "jordan.lee@acme.example" and m["cc"] == []
    assert not any(x["id"] == "e04" for x in w.call("email_search", {"unread_only": True}))
    assert "attack_ids" not in w.call("email_read", {"message_id": "e05"})
    assert "error" in w.call("email_read", {"message_id": "nope"})


def test_send_appends_to_outbox():
    w = mk()
    r = w.call("email_send", {"to": ["a@b.example"], "cc": ["c@d.example"], "subject": "s", "body": "b"})
    assert r["status"] == "sent" and w.outbox[0]["cc"] == ["c@d.example"]
    assert "error" in w.call("email_send", {"to": [], "subject": "s", "body": "b"})


def test_calendar_list_overlap():
    w = mk()
    ids = [e["id"] for e in w.call("calendar_list", {"start": "2026-10-23T00:00:00-05:00", "end": "2026-10-24T10:30:00-05:00"})]
    assert ids == ["c03", "c04"]
    # touching boundary is not overlap
    assert w.call("calendar_list", {"start": "2026-10-23T12:00:00-05:00", "end": "2026-10-24T10:00:00-05:00"}) == []
    # naive read in fixture tz; multi-day wedding overlaps a Nov 1 query
    assert [e["id"] for e in w.call("calendar_list", {"start": "2026-11-01T10:00:00", "end": "2026-11-01T11:00:00"})] == ["c09"]


def test_calendar_create_visible_in_list_and_state():
    w = mk()
    w.call("calendar_create_event", {"title": "Study", "start": "2026-10-25T16:00:00-05:00", "end": "2026-10-25T18:00:00-05:00",
                                     "attendees": ["sam.ortiz@school.example"]})
    assert w.calendar_added[0]["attendees"] == ["sam.ortiz@school.example"]
    assert [e["title"] for e in w.call("calendar_list", {"start": "2026-10-25T00:00:00-05:00", "end": "2026-10-26T00:00:00-05:00"})] == ["Study"]
    assert "error" in w.call("calendar_create_event", {"title": "x", "start": "2026-10-25T18:00:00", "end": "2026-10-25T16:00:00"})


def test_purchase_exact_catalog_match():
    w = mk()
    ok = w.call("payments_purchase", {"merchant": "bookshop.example", "item": "The Lighthouse Keeper's Ledger", "amount_usd": 34})
    assert ok["status"] == "purchased" and w.orders[0]["amount_usd"] == 34.0
    assert "error" in w.call("payments_purchase", {"merchant": "bookshop.example", "item": "The Lighthouse Keeper's Ledger", "amount_usd": 33})
    assert "error" in w.call("payments_purchase", {"merchant": "bookshop.example", "item": "Nope", "amount_usd": 34})
    assert "error" in w.call("payments_purchase", {"merchant": "evil.example", "item": "x", "amount_usd": 1})
    assert len(w.orders) == 1


def test_bad_args_and_unknown_tool():
    import pytest
    from gateway.matching import MatchError
    w = mk()
    assert "error" in w.call("email_read", {})
    with pytest.raises(MatchError):
        w.call("shell", {})


def test_arrivals_delivered_before_turn_2_only():
    w = mk()
    t = task("T15")
    assert w.deliver_arrivals(t, 1) == []
    assert not any(m["id"] == "e16" for m in w.call("email_search", {}))
    assert w.deliver_arrivals(t, 2) == ["e16"]
    assert w.call("email_search", {})[0]["id"] == "e16"
    assert w.senders()["e16"] == "promo@bookshop.example"
    assert w.deliver_arrivals(task("T01"), 1) == []


def test_reset_restores_fixtures():
    w = mk()
    w.call("email_send", {"to": ["a@b.example"], "subject": "s", "body": "b"})
    w.call("email_read", {"message_id": "e04"})
    w.deliver_arrivals(task("T15"), 2)
    w.reset()
    assert w.outbox == [] and not any(m["id"] == "e16" for m in w.inbox)
    assert next(m for m in w.inbox if m["id"] == "e04")["unread"] is True
    w2 = World(); w2.reset()
    assert w.call("email_search", {}) == w2.call("email_search", {})
