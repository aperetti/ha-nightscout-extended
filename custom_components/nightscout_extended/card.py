"""Serves the bundled Lovelace card and loads it on every dashboard."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = Path(__file__).parent / "www" / "nightscout-glucose-card.js"
CARD_URL = f"/{DOMAIN}/nightscout-glucose-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and add it to the frontend, so no dashboard resource is needed.

    Every normal install loads `http` and `frontend` (they are after_dependencies, so they
    are set up first); without them, e.g. in a headless test setup, there is nothing to do.
    """
    if hass.http is None or "frontend" not in hass.config.components:
        _LOGGER.debug("Frontend not loaded; glucose card not registered")
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)]
    )
    # The version in the URL makes browsers fetch the new card after an update.
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    _LOGGER.debug("Glucose card served at %s", CARD_URL)
