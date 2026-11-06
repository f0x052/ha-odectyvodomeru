"""Verify bundled icons through Home Assistant's authenticated brands API."""

from pathlib import Path
from struct import unpack

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

BRAND = Path(__file__).parents[1] / "custom_components" / "odectyvodomeru" / "brand"


@pytest.mark.parametrize(
    ("image", "size"),
    [
        ("icon.png", 256),
        ("icon@2x.png", 512),
        ("dark_icon.png", 256),
        ("dark_icon@2x.png", 512),
    ],
)
def test_explicit_icon_asset(image: str, size: int) -> None:
    """Ship each icon URL directly as a square RGBA PNG, without fallback."""
    data = (BRAND / image).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    assert unpack(">IIBB", data[16:26]) == (size, size, 8, 6)


@pytest.mark.parametrize(
    ("image", "source"),
    [
        ("icon.png", "icon.png"),
        ("icon@2x.png", "icon@2x.png"),
        ("dark_icon.png", "icon.png"),
        ("dark_icon@2x.png", "icon@2x.png"),
        ("logo.png", "icon.png"),
        ("logo@2x.png", "icon.png"),
        ("dark_logo.png", "icon.png"),
        ("dark_logo@2x.png", "icon@2x.png"),
    ],
)
async def test_brand_endpoint(
    hass: HomeAssistant, hass_client, image: str, source: str
) -> None:
    """Resolve light/dark and normal/high-resolution images without the CDN."""
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    response = await client.get(
        f"/api/brands/integration/odectyvodomeru/{image}?placeholder=no"
    )
    assert response.status == 200
    assert response.content_type == "image/png"
    assert await response.read() == (BRAND / source).read_bytes()
