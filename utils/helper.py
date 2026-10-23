import re
from datetime import datetime, timezone
from typing import Optional

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def format_duration(seconds: int) -> str:
    """MM:SS below one hour, H:MM:SS above."""
    seconds = max(int(seconds or 0), 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def pace_seconds_per_km(duration_seconds: int, distance_km: float) -> int:
    if not distance_km or distance_km < 0.01 or not duration_seconds:
        return 0
    return int(round(duration_seconds / distance_km))


def format_pace(pace_seconds: Optional[int]) -> str:
    """Seconds per km -> M'SS\"."""
    if not pace_seconds or pace_seconds <= 0:
        return "0'00\""
    minutes, secs = divmod(int(pace_seconds), 60)
    return f"{minutes}'{secs:02d}\""


def normalize_username(username: str) -> str:
    return username.strip()


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))
