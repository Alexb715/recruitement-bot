"""Unit tests for the application-lookup DB helpers backing the accept-time
nickname (`latest_accepted_community_name`) and recruitment-server graduation
(`accepted_users_decided_before`) features. Uses a throwaway sqlite file per
test via pytest's `tmp_path`; no Discord involved."""

from __future__ import annotations

from datetime import datetime, timezone

from db import (
    accepted_users_decided_before,
    clear_application_decision,
    get_application,
    init_db,
    latest_accepted_answers,
    latest_accepted_community_name,
    latest_rejection_for_user,
    save_application,
    set_application_decision,
    set_results_message_id,
)


def _submit(db_path, *, user_id, community_name, when, username="user"):
    return save_application(
        db_path,
        user_id=user_id,
        username=username,
        started_at=when,
        completed_at=when,
        answers={"community_name": community_name},
    )


def _accept(db_path, *, user_id, community_name, decided_at, username="user"):
    app_id = _submit(
        db_path,
        user_id=user_id,
        community_name=community_name,
        when=decided_at,
        username=username,
    )
    set_application_decision(
        db_path,
        application_id=app_id,
        status="accepted",
        decided_by=1,
        decided_at=decided_at,
    )
    return app_id


def _reject(db_path, *, user_id, decided_at, reason="nope", username="user"):
    app_id = _submit(
        db_path,
        user_id=user_id,
        community_name="J. Smith",
        when=decided_at,
        username=username,
    )
    set_application_decision(
        db_path,
        application_id=app_id,
        status="rejected",
        decided_by=1,
        decided_at=decided_at,
        reason=reason,
    )
    return app_id


# --- latest_accepted_answers -------------------------------------------------


def test_latest_accepted_answers_returns_newest_accepted_answers(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _accept(
        db,
        user_id=42,
        community_name="Old Name",
        decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _accept(
        db,
        user_id=42,
        community_name="New Name",
        decided_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert latest_accepted_answers(db, 42) == {"community_name": "New Name"}


def test_latest_accepted_answers_none_when_not_accepted(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _submit(db, user_id=7, community_name="J. Smith", when=datetime.now(timezone.utc))
    _reject(db, user_id=8, decided_at=datetime.now(timezone.utc))
    assert latest_accepted_answers(db, 7) is None
    assert latest_accepted_answers(db, 8) is None
    assert latest_accepted_answers(db, 999) is None


# --- latest_accepted_community_name -----------------------------------------


def test_latest_accepted_community_name_returns_name(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime.now(timezone.utc),
    )
    assert latest_accepted_community_name(db, 42) == "R. Sutherland"


def test_latest_accepted_community_name_none_when_only_submitted(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _submit(db, user_id=7, community_name="J. Smith", when=datetime.now(timezone.utc))
    assert latest_accepted_community_name(db, 7) is None


def test_latest_accepted_community_name_none_without_application(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    assert latest_accepted_community_name(db, 999) is None


def test_latest_accepted_community_name_picks_newest_acceptance(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _accept(
        db,
        user_id=42,
        community_name="Old Name",
        decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _accept(
        db,
        user_id=42,
        community_name="New Name",
        decided_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert latest_accepted_community_name(db, 42) == "New Name"


# --- accepted_users_decided_before ------------------------------------------


def test_accepted_users_decided_before_includes_old_acceptance(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert accepted_users_decided_before(db, cutoff) == ["42"]


def test_accepted_users_decided_before_excludes_recent_acceptance(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert accepted_users_decided_before(db, cutoff) == []


def test_accepted_users_decided_before_excludes_non_accepted(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    _submit(
        db,
        user_id=7,
        community_name="J. Smith",
        when=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cutoff = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert accepted_users_decided_before(db, cutoff) == []


def test_accepted_users_decided_before_uses_latest_decision_per_user(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    # Re-accepted recently: the newer acceptance resets the graduation clock, so
    # this user should NOT be graduated even though an older acceptance exists.
    _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
    )
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert accepted_users_decided_before(db, cutoff) == []


# --- clear_application_decision (reverse) ------------------------------------


def test_clear_application_decision_resets_status_and_columns(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    app_id = _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime.now(timezone.utc),
    )
    clear_application_decision(db, application_id=app_id)
    app = get_application(db, app_id)
    assert app["status"] == "submitted"
    assert app["decided_at"] is None
    assert app["decided_by"] is None
    assert app["decision_reason"] is None


def test_clear_application_decision_lifts_embargo(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    app_id = _reject(
        db, user_id=7, decided_at=datetime.now(timezone.utc), reason="try later"
    )
    assert latest_rejection_for_user(db, 7) is not None
    clear_application_decision(db, application_id=app_id)
    assert latest_rejection_for_user(db, 7) is None


def test_clear_application_decision_drops_from_graduation(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    app_id = _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert accepted_users_decided_before(db, cutoff) == ["42"]
    clear_application_decision(db, application_id=app_id)
    assert accepted_users_decided_before(db, cutoff) == []


def test_clear_application_decision_clears_accepted_community_name(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    app_id = _accept(
        db,
        user_id=42,
        community_name="R. Sutherland",
        decided_at=datetime.now(timezone.utc),
    )
    assert latest_accepted_community_name(db, 42) == "R. Sutherland"
    clear_application_decision(db, application_id=app_id)
    assert latest_accepted_community_name(db, 42) is None


def test_clear_application_decision_unknown_id_is_noop(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    # Should not raise, and should touch nothing.
    clear_application_decision(db, application_id=999)


# --- set_results_message_id --------------------------------------------------


def test_set_results_message_id_roundtrips(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    app_id = _submit(
        db, user_id=7, community_name="J. Smith", when=datetime.now(timezone.utc)
    )
    set_results_message_id(
        db, application_id=app_id, message_id=123456789012345678
    )
    app = get_application(db, app_id)
    assert app["results_message_id"] == "123456789012345678"
