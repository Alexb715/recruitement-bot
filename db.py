import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    username        TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    completed_at    TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    answers_json    TEXT    NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT,
    decision_reason TEXT,
    results_message_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_user_id
    ON applications(user_id);

CREATE INDEX IF NOT EXISTS idx_applications_completed_at
    ON applications(completed_at);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS member_activity (
    user_id       TEXT PRIMARY KEY,
    last_activity TEXT NOT NULL,
    warned_at     TEXT
);
"""

# Columns added after the initial schema. Applied via ALTER TABLE on startup so
# databases created before the decision-tracking feature pick them up without a
# separate migration step.
_DECISION_COLUMNS = (
    ("decided_at", "TEXT"),
    ("decided_by", "TEXT"),
    ("decision_reason", "TEXT"),
)

# Every column added to `applications` after its initial schema, applied via the
# same defensive ALTER TABLE loop so pre-existing databases pick them up without
# a migration step. `results_message_id` records the staff-channel message a
# decision was posted to, so `/reverse-decision` can find and rebuild it.
_ADDED_COLUMNS = _DECISION_COLUMNS + (("results_message_id", "TEXT"),)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for column, ctype in _ADDED_COLUMNS:
            try:
                conn.execute(
                    f"ALTER TABLE applications ADD COLUMN {column} {ctype}"
                )
            except sqlite3.OperationalError:
                # Column already exists — SQLite has no ADD COLUMN IF NOT EXISTS.
                pass


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_application(
    db_path: Path,
    *,
    user_id: int,
    username: str,
    started_at: datetime,
    completed_at: datetime,
    answers: dict[str, Any],
    status: str = "submitted",
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO applications
                (user_id, username, started_at, completed_at, status, answers_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(user_id),
                username,
                started_at.isoformat(),
                completed_at.isoformat(),
                status,
                json.dumps(answers, ensure_ascii=False),
            ),
        )
        return int(cursor.lastrowid)


def list_applications(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, username, started_at, completed_at, status
            FROM applications
            ORDER BY completed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_application(db_path: Path, application_id: int) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["answers"] = json.loads(record.pop("answers_json"))
    return record


def set_application_decision(
    db_path: Path,
    *,
    application_id: int,
    status: str,
    decided_by: int,
    decided_at: datetime,
    reason: str | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE applications
            SET status = ?, decided_at = ?, decided_by = ?, decision_reason = ?
            WHERE id = ?
            """,
            (
                status,
                decided_at.isoformat(),
                str(decided_by),
                reason,
                application_id,
            ),
        )


def clear_application_decision(db_path: Path, *, application_id: int) -> None:
    """Undo a recorded decision: return the application to 'submitted' and null
    every decision column. Flipping status alone already neutralizes the
    status-keyed reads (reapply embargo, graduation scan, accept-time nickname),
    but nulling decided_at/decided_by/decision_reason leaves the row
    indistinguishable from a fresh submission so nothing downstream trips over a
    stale decider or leftover reason. Used when staff reverse a mis-clicked
    accept/reject so the application can be decided again. Clearing an unknown id
    is a harmless no-op."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE applications
            SET status = 'submitted',
                decided_at = NULL,
                decided_by = NULL,
                decision_reason = NULL
            WHERE id = ?
            """,
            (application_id,),
        )


def set_results_message_id(
    db_path: Path, *, application_id: int, message_id: int
) -> None:
    """Record the staff-channel message id the application's results embed was
    posted to, so `/reverse-decision` (which has no button to ride on) can fetch
    and rebuild that message."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE applications SET results_message_id = ? WHERE id = ?",
            (str(message_id), application_id),
        )


def latest_rejection_for_user(
    db_path: Path, user_id: int
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, decided_at, decision_reason
            FROM applications
            WHERE user_id = ? AND status = 'rejected' AND decided_at IS NOT NULL
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            (str(user_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def latest_accepted_community_name(
    db_path: Path, user_id: int
) -> str | None:
    """Return the `community_name` answer from this user's most recent ACCEPTED
    application, or None if they have no accepted application (or it lacks a
    usable community_name). Used to nickname accepted applicants when they join
    a community server."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT answers_json
            FROM applications
            WHERE user_id = ? AND status = 'accepted' AND decided_at IS NOT NULL
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            (str(user_id),),
        ).fetchone()
    if row is None:
        return None
    try:
        answers = json.loads(row["answers_json"])
    except (ValueError, TypeError):
        return None
    value = answers.get("community_name")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def accepted_users_decided_before(
    db_path: Path, cutoff: datetime
) -> list[str]:
    """Return the user_ids whose most recent acceptance was decided at or before
    `cutoff` - i.e. accepted members due to be graduated off the recruitment
    server. A later acceptance for the same user resets the clock (MAX pushes the
    grouped value past the cutoff), so re-accepted members are excluded."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT user_id, MAX(decided_at) AS last_decided
            FROM applications
            WHERE status = 'accepted' AND decided_at IS NOT NULL
            GROUP BY user_id
            HAVING last_decided <= ?
            """,
            (cutoff.isoformat(),),
        ).fetchall()
    return [row["user_id"] for row in rows]


def get_state(db_path: Path, key: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row is not None else None


def set_state(db_path: Path, key: str, value: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def delete_state(db_path: Path, key: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM bot_state WHERE key = ?", (key,))


def record_activity(db_path: Path, user_id: int, when: datetime) -> None:
    """Mark a member as active at `when`, clearing any pending inactivity
    warning - becoming active restarts the 14-day clock from scratch."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO member_activity (user_id, last_activity, warned_at)
            VALUES (?, ?, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                last_activity = excluded.last_activity,
                warned_at = NULL
            """,
            (str(user_id), when.isoformat()),
        )


def get_member_activity(db_path: Path, user_id: int) -> dict[str, Any] | None:
    """Return {'last_activity': datetime, 'warned_at': datetime | None} for a
    member, or None if we've never recorded any activity for them."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_activity, warned_at FROM member_activity WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
    if row is None:
        return None
    return {
        "last_activity": datetime.fromisoformat(row["last_activity"]),
        "warned_at": (
            datetime.fromisoformat(row["warned_at"]) if row["warned_at"] else None
        ),
    }


def seed_activity_if_absent(db_path: Path, user_id: int, when: datetime) -> None:
    """Seed a member's last_activity only if no row exists yet. Used to backfill
    current prospects on startup so they get a fresh window instead of being
    judged inactive immediately."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO member_activity (user_id, last_activity, warned_at)
            VALUES (?, ?, NULL)
            """,
            (str(user_id), when.isoformat()),
        )


def mark_warned(
    db_path: Path, user_id: int, last_activity: datetime, warned_at: datetime
) -> None:
    """Record that an inactivity warning was sent. If no row exists yet (member
    never tracked, inactivity measured from join date), seed last_activity with
    the provided value so the kick clock is preserved; otherwise only update
    warned_at and leave last_activity untouched."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO member_activity (user_id, last_activity, warned_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET warned_at = excluded.warned_at
            """,
            (str(user_id), last_activity.isoformat(), warned_at.isoformat()),
        )


def delete_member_activity(db_path: Path, user_id: int) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM member_activity WHERE user_id = ?", (str(user_id),)
        )
