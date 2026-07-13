"""Unit tests guarding the interview question set: the rebuild-disclaimer
acknowledgment question, and the `community_name` key the accept-time / on-join
rename feature reads from the stored answers."""

from __future__ import annotations

from questions import QUESTIONS, by_key


def _index(key: str) -> int:
    return next(i for i, q in enumerate(QUESTIONS) if q.key == key)


def test_rebuild_acknowledgment_is_yes_no():
    assert by_key("rebuild_acknowledgment").kind == "yes_no"


def test_rebuild_acknowledgment_placed_after_rules_before_code():
    assert (
        _index("rules_agreement")
        < _index("rebuild_acknowledgment")
        < _index("knows_code")
    )


def test_community_name_question_still_exists():
    # The rename reads answers["community_name"]; guard the key against an
    # accidental rename that would silently break the feature.
    assert by_key("community_name").key == "community_name"
