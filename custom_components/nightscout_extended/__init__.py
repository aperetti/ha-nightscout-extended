"""Nightscout Extended: glucose, insulin on board, carbs on board and forecast from Nightscout."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NightscoutAuthError, NightscoutClient, NightscoutConnectionError
from .const import CONF_USE_API_SECRET, UNIT_MGDL, UNIT_MMOL
from .coordinator import NightscoutConfigEntry, NightscoutCoordinator

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: NightscoutConfigEntry) -> bool:
    client = NightscoutClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data.get(CONF_API_KEY)
    )
    client.set_uses_api_secret(entry.data.get(CONF_USE_API_SECRET, False))
    try:
        status = await client.async_get_status()
    except NightscoutAuthError as err:
        raise ConfigEntryAuthFailed("Nightscout rejected the token or API secret") from err
    except NightscoutConnectionError as err:
        raise ConfigEntryNotReady(f"Could not reach Nightscout: {err}") from err

    units = (status.get("settings") or {}).get("units", "mg/dl")
    coordinator = NightscoutCoordinator(
        hass, entry, client, UNIT_MMOL if str(units).lower().startswith("mmol") else UNIT_MGDL
    )
    coordinator.status = status
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: NightscoutConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NightscoutConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
