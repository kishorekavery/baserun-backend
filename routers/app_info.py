import os

from fastapi import APIRouter

from db.database import DEFAULT_SEASON, connection
from models.datamodel import AppConfigResponse, ColorPaletteResponse
from services.colors import COLOR_PALETTE
from utils.helper import iso, utcnow

router = APIRouter(prefix="/api/v1/app", tags=["app"])

APP_NAME = "BASER"
TAGLINE = "Turn Your Miles into Territory"
WELCOME_QUOTE = "Run. Explore. Claim. Belong."


def _env_flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


@router.get("/config", response_model=AppConfigResponse)
async def get_app_config():
    """
    Screen 1: brand info, current season, maintenance flag and minimum app version.
    MAINTENANCE_MODE, APP_VERSION and APP_MIN_VERSION are read from the environment.
    """
    now = utcnow()
    async with connection() as conn:
        season = await conn.fetchval(
            """
            SELECT name FROM seasons
            WHERE starts_at <= $1 AND (ends_at IS NULL OR ends_at > $1)
            ORDER BY starts_at DESC LIMIT 1
            """,
            now,
        )
    season = season or DEFAULT_SEASON[0]
    return {
        "app_name": APP_NAME,
        "tagline": TAGLINE,
        "current_season": season,
        "active_season": season,
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "min_version": os.getenv("APP_MIN_VERSION", "1.0.0"),
        "maintenance_mode": _env_flag("MAINTENANCE_MODE"),
        "server_time": iso(now),
        "welcome_quote": WELCOME_QUOTE,
    }


@router.get("/color-palette", response_model=ColorPaletteResponse)
async def get_color_palette():
    """
    Screen 4: territory color swatches.
    """
    return {"colors": COLOR_PALETTE}
