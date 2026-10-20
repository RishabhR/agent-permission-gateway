import pytest

from harness.run_eval import parse_flags


def test_parse_flags():
    assert parse_flags("") == {}
    assert parse_flags("defer_unkeyed_writes=true,denial_style=recoverable") == {
        "defer_unkeyed_writes": True, "denial_style": "recoverable"}
    assert parse_flags("defer_unkeyed_writes=false") == {"defer_unkeyed_writes": False}


@pytest.mark.parametrize("bad", ["defer_unkeyed_writes=maybe", "nope=1", "denial_style=gentle", "defer_unkeyed_writes"])
def test_bad_flags_fail_loudly(bad):
    with pytest.raises(SystemExit):
        parse_flags(bad)
