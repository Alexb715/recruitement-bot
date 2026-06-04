"""Unit tests for the pure config-parsing helpers used to schedule the
recurring prospect ping at fixed clock times."""

from __future__ import annotations

from datetime import time

import pytest

from config import parse_ping_times, parse_timezone


def test_parse_timezone_valid():
    tz = parse_timezone("America/Toronto")
    assert tz.key == "America/Toronto"


def test_parse_timezone_invalid_raises():
    with pytest.raises(RuntimeError):
        parse_timezone("Not/AZone")


def test_parse_ping_times_basic():
    tz = parse_timezone("America/Toronto")
    times = parse_ping_times("09:00,21:00", tz)
    assert [t.hour for t in times] == [9, 21]
    assert [t.minute for t in times] == [0, 0]


def test_parse_ping_times_attaches_tzinfo():
    tz = parse_timezone("America/Toronto")
    times = parse_ping_times("09:00,21:00", tz)
    assert all(t.tzinfo is tz for t in times)


def test_parse_ping_times_tolerates_whitespace_and_short_hours():
    tz = parse_timezone("America/Toronto")
    times = parse_ping_times(" 9:00 , 21:30 ", tz)
    assert [(t.hour, t.minute) for t in times] == [(9, 0), (21, 30)]


def test_parse_ping_times_single():
    tz = parse_timezone("America/Toronto")
    times = parse_ping_times("09:00", tz)
    assert [(t.hour, t.minute) for t in times] == [(9, 0)]


def test_parse_ping_times_tolerates_trailing_comma():
    tz = parse_timezone("America/Toronto")
    times = parse_ping_times("09:00,", tz)
    assert [(t.hour, t.minute) for t in times] == [(9, 0)]


@pytest.mark.parametrize("raw", ["9", "25:00", "abc", "09:60", "", "   ", ","])
def test_parse_ping_times_rejects_malformed(raw):
    tz = parse_timezone("America/Toronto")
    with pytest.raises(RuntimeError):
        parse_ping_times(raw, tz)
