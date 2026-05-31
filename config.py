import os
from dataclasses import dataclass
from pathlib import Path

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
    dev_guild_id: int | None
    # Multi-server invites issued to accepted applicants.
    main_server_invite_channel_id: int | None
    opp_server_invite_channel_id: int | None
    # Prospect management: recurring ping + inactivity auto-kick.
    prospect_management_enabled: bool
    prospect_role_id: int | None
    prospect_ping_channel_id: int | None
    prospect_ping_interval_hours: int
    inactivity_warn_days: int
    inactivity_kick_days: int
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


def load_config() -> Config:
    db_path = Path(os.environ.get("DB_PATH", "data/applications.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
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
        dev_guild_id=_optional_int("DEV_GUILD_ID"),
        main_server_invite_channel_id=_optional_int("MAIN_SERVER_INVITE_CHANNEL_ID"),
        opp_server_invite_channel_id=_optional_int("OPP_SERVER_INVITE_CHANNEL_ID"),
        prospect_management_enabled=_optional_bool("PROSPECT_MANAGEMENT_ENABLED", False),
        prospect_role_id=_optional_int("PROSPECT_ROLE_ID"),
        prospect_ping_channel_id=_optional_int("PROSPECT_PING_CHANNEL_ID"),
        prospect_ping_interval_hours=_optional_int_default(
            "PROSPECT_PING_INTERVAL_HOURS", 12
        ),
        inactivity_warn_days=_optional_int_default("INACTIVITY_WARN_DAYS", 12),
        inactivity_kick_days=_optional_int_default("INACTIVITY_KICK_DAYS", 14),
        db_path=db_path,
    )
