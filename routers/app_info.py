from fastapi import APIRouter
from models.datamodel import AppConfigResponse, ColorPaletteResponse, ColorPaletteItem

router = APIRouter(prefix="/api/v1/app", tags=["app"])


@router.get("/config", response_model=AppConfigResponse)
async def get_app_config():
    """
    Screen 1: Splash Screen API
    Returns app brand info, tagline, active season, and welcome quote.
    """
    return {
        "app_name": "Run to Claim",
        "tagline": "YOUR RUN. YOUR TERRITORY. A BIGGER WORLD.",
        "version": "1.0.0",
        "active_season": "Season 1: Coastal Turf Wars",
        "welcome_quote": "Run. Explore. Claim. Belong."
    }


@router.get("/color-palette", response_model=ColorPaletteResponse)
async def get_color_palette():
    """
    Screen 4: Choose Your Identity Color Palette
    Returns the curated 10 territory color swatches shown on Screen 4.
    """
    return {
        "colors": [
            ColorPaletteItem(name="Neon Cyan", hex="#00E5FF"),
            ColorPaletteItem(name="Electric Magenta", hex="#E91E63"),
            ColorPaletteItem(name="Solar Orange", hex="#FF9100"),
            ColorPaletteItem(name="Volt Lime", hex="#76FF03"),
            ColorPaletteItem(name="Sunburst Yellow", hex="#FFEA00"),
            ColorPaletteItem(name="Ocean Blue", hex="#00B0FF"),
            ColorPaletteItem(name="Deep Purple", hex="#7C4DFF"),
            ColorPaletteItem(name="Crimson Red", hex="#FF1744"),
            ColorPaletteItem(name="Hot Pink", hex="#F50057"),
            ColorPaletteItem(name="Stealth Charcoal", hex="#37474F"),
        ]
    }
