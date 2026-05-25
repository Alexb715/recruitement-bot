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
    dev_guild_id: int | None
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


def load_config() -> Config:
    db_path = Path(os.environ.get("DB_PATH", "data/applications.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Config(
        token=_require("DISCORD_TOKEN"),
        results_channel_id=_require_int("RESULTS_CHANNEL_ID"),
        recruiter_role_id=_require_int("RECRUITER_ROLE_ID"),
        apply_channel_id=_optional_int("APPLY_CHANNEL_ID"),
        dev_guild_id=_optional_int("DEV_GUILD_ID"),
        db_path=db_path,
    )
