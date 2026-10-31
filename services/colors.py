"""
Territory color palette and the premium color rule.
"""
from models.datamodel import ColorPaletteItem

COLOR_PALETTE = [
    ColorPaletteItem(name="Electric Cyan", hex="#00E5FF"),
    ColorPaletteItem(name="Neon Lime", hex="#76FF03"),
    ColorPaletteItem(name="Hot Magenta", hex="#FF007F"),
    ColorPaletteItem(name="Cyber Purple", hex="#7928CA"),
    ColorPaletteItem(name="Blaze Orange", hex="#FF5722"),
    ColorPaletteItem(name="Blaze Gold", hex="#FFD700", is_premium=True),
]

# RGB distance under which two colors look the same on the map, so that
# #FFD701 can't be used to get a premium color for free
PREMIUM_LOOKALIKE_DISTANCE = 40


def _rgb(hex_color: str) -> tuple:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def is_premium_color(hex_color: str) -> bool:
    rgb = _rgb(hex_color)
    return any(
        sum((a - b) ** 2 for a, b in zip(rgb, _rgb(item.hex))) < PREMIUM_LOOKALIKE_DISTANCE ** 2
        for item in COLOR_PALETTE if item.is_premium
    )
