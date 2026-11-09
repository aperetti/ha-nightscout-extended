"""Diagnostics for Nightscout Extended.

People attach these to public GitHub issues, so they hold no credentials, no URL and no health values:
only which values Nightscout returned.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

from .coordinator import NightscoutConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_URL, "customTitle", "baseURL"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: NightscoutConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    status = coordinator.status or {}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "nightscout": {
            "version": status.get("version"),
            "units": (status.get("settings") or {}).get("units"),
            "enabled_plugins": (status.get("settings") or {}).get("enable"),
        },
        "values_present": (
            {k: (len(v) if isinstance(v, list) else v is not None) for k, v in asdict(coordinator.data).items()}
            if coordinator.data else None
        ),
    }
