import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent


@dataclass(frozen=True)
class Config:
    token: str
    results_channel_id: int
    recruiter_role_id: int
    apply_channel_id: int | None
    faq_channel_id: int | None
    requirements_channel_id: int | None
    faq_interview_request_channel_id: int | None
    faq_rules_channel_id: int | None
    faq_opp_recruitment_channel_id: int | None
    faq_support_role_id: int | None
    # Help/Ticket button: the category new tickets are created under, and the
    # category they're moved to when closed. Tickets are visible to (and ping)
    # the FAQ support role. Requires the bot to have Manage Channels + Manage
    # Roles.
    ticket_category_id: int | None
    ticket_closed_category_id: int | None
    dev_guild_id: int | None
    # Multi-server invites issued to accepted applicants.
    main_server_invite_channel_id: int | None
    opp_server_invite_channel_id: int | None
    # Prospect management: recurring ping + inactivity auto-kick.
    prospect_management_enabled: bool
    prospect_role_id: int | None
    prospect_ping_channel_id: int | None
    # Fixed daily clock times (tz-aware) at which the recurring prospect ping
    # fires, e.g. 9 AM and 9 PM Eastern.
    prospect_ping_timezone: ZoneInfo
    prospect_ping_times: tuple[time, ...]
    inactivity_warn_days: int
    inactivity_kick_days: int
    # Bot presence shown under its name, e.g. "Watching for new applicants".
    bot_activity_type: str
    bot_activity_text: str
    db_path: Path


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer (got {raw!r})") from exc


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer (got {raw!r})") from exc


def _optional_int_default(name: str, default: int) -> int:
    value = _optional_int(name)
    return default if value is None else value


def _optional_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


def parse_timezone(raw: str) -> ZoneInfo:
    """Resolve an IANA time-zone name (e.g. "America/Toronto") to a ZoneInfo,
    raising a clear error if it can't be found."""
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(
            f"Unknown time zone {raw!r} for PROSPECT_PING_TIMEZONE"
        ) from exc


def parse_ping_times(raw: str, tz: ZoneInfo) -> tuple[time, ...]:
    """Parse a comma-separated list of HH:MM clock times into tz-aware
    `datetime.time`s for fixed daily scheduling. Empty segments are ignored;
    malformed entries or a total absence of times raise a clear error."""
    times: list[time] = []
    for chunk in raw.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        parts = piece.split(":")
        if len(parts) != 2 or not all(p.strip() for p in parts):
            raise RuntimeError(
                f"Invalid time {piece!r} in PROSPECT_PING_TIMES (expected HH:MM)"
            )
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid time {piece!r} in PROSPECT_PING_TIMES (expected HH:MM)"
            ) from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise RuntimeError(
                f"Time {piece!r} in PROSPECT_PING_TIMES is out of range "
                "(00:00-23:59)"
            )
        times.append(time(hour=hour, minute=minute, tzinfo=tz))
    if not times:
        raise RuntimeError(
            "PROSPECT_PING_TIMES must list at least one HH:MM time"
        )
    return tuple(times)


def load_config() -> Config:
    db_path = Path(os.environ.get("DB_PATH", "data/applications.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    ping_tz = parse_timezone(
        os.environ.get("PROSPECT_PING_TIMEZONE", "").strip() or "America/Toronto"
    )
    ping_times = parse_ping_times(
        os.environ.get("PROSPECT_PING_TIMES", "").strip() or "09:00,21:00",
        ping_tz,
    )
    return Config(
        token=_require("DISCORD_TOKEN"),
        results_channel_id=_require_int("RESULTS_CHANNEL_ID"),
        recruiter_role_id=_require_int("RECRUITER_ROLE_ID"),
        apply_channel_id=_optional_int("APPLY_CHANNEL_ID"),
        faq_channel_id=_optional_int("FAQ_CHANNEL_ID"),
        requirements_channel_id=_optional_int("REQUIREMENTS_CHANNEL_ID"),
        faq_interview_request_channel_id=_optional_int("FAQ_INTERVIEW_REQUEST_CHANNEL_ID"),
        faq_rules_channel_id=_optional_int("FAQ_RULES_CHANNEL_ID"),
        faq_opp_recruitment_channel_id=_optional_int("FAQ_OPP_RECRUITMENT_CHANNEL_ID"),
        faq_support_role_id=_optional_int("FAQ_SUPPORT_ROLE_ID"),
        ticket_category_id=_optional_int("TICKET_CATEGORY_ID"),
        ticket_closed_category_id=_optional_int("TICKET_CLOSED_CATEGORY_ID"),
        dev_guild_id=_optional_int("DEV_GUILD_ID"),
        main_server_invite_channel_id=_optional_int("MAIN_SERVER_INVITE_CHANNEL_ID"),
        opp_server_invite_channel_id=_optional_int("OPP_SERVER_INVITE_CHANNEL_ID"),
        prospect_management_enabled=_optional_bool("PROSPECT_MANAGEMENT_ENABLED", False),
        prospect_role_id=_optional_int("PROSPECT_ROLE_ID"),
        prospect_ping_channel_id=_optional_int("PROSPECT_PING_CHANNEL_ID"),
        prospect_ping_timezone=ping_tz,
        prospect_ping_times=ping_times,
        inactivity_warn_days=_optional_int_default("INACTIVITY_WARN_DAYS", 12),
        inactivity_kick_days=_optional_int_default("INACTIVITY_KICK_DAYS", 14),
        bot_activity_type=os.environ.get("BOT_ACTIVITY_TYPE", "").strip().lower()
        or "watching",
        bot_activity_text=os.environ.get("BOT_ACTIVITY_TEXT", "").strip()
        or "for new applicants",
        db_path=db_path,
    )
