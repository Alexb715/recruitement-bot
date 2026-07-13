"""Unit tests for the pure `clean_nickname` helper that derives a Discord
nickname from an applicant's `community_name` answer. The Discord-side rename
(permissions, hierarchy, owner guard) is verified manually in a dev guild."""

from __future__ import annotations

from cogs.interview import NICK_MAX_LEN, clean_nickname


def test_clean_nickname_none_for_empty_inputs():
    assert clean_nickname(None) is None
    assert clean_nickname("") is None
    assert clean_nickname("   ") is None


def test_clean_nickname_strips_surrounding_whitespace():
    assert clean_nickname("  R. Sutherland  ") == "R. Sutherland"


def test_clean_nickname_truncates_to_discord_limit():
    result = clean_nickname("x" * (NICK_MAX_LEN + 8))
    assert result == "x" * NICK_MAX_LEN
    assert len(result) == NICK_MAX_LEN


def test_clean_nickname_leaves_normal_name_unchanged():
    assert clean_nickname("R. Sutherland") == "R. Sutherland"
