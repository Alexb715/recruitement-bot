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
    decision_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_user_id
    ON applications(user_id);

CREATE INDEX IF NOT EXISTS idx_applications_completed_at
    ON applications(completed_at);

CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for column, ctype in _DECISION_COLUMNS:
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
