"""Polls Nightscout."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NightscoutAuthError, NightscoutClient, NightscoutConnectionError, NightscoutData
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

type NightscoutConfigEntry = ConfigEntry[NightscoutCoordinator]


class NightscoutCoordinator(DataUpdateCoordinator[NightscoutData]):
    """Fetches all values in one request."""

    config_entry: NightscoutConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NightscoutConfigEntry, client: NightscoutClient, units: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.units = units
        self.status: dict = {}

    async def _async_update_data(self) -> NightscoutData:
        try:
            return await self.client.async_get_data()
        except NightscoutAuthError as err:
            raise ConfigEntryAuthFailed("Nightscout rejected the token or API secret") from err
        except NightscoutConnectionError as err:
            raise UpdateFailed(f"Could not read Nightscout: {err}") from err
