"""Config flow for Nightscout Extended."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import NightscoutAuthError, NightscoutClient, NightscoutConnectionError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_USE_API_SECRET,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    return url


class NightscoutExtendedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the Nightscout URL and a token or API secret."""

    VERSION = 1

    async def _validate(self, url: str, credential: str | None) -> tuple[dict[str, Any], bool, str | None]:
        """Returns (status, uses_api_secret, error key)."""
        client = NightscoutClient(async_get_clientsession(self.hass), url, credential)
        try:
            status = await client.async_get_status()
        except NightscoutAuthError:
            return {}, False, "invalid_auth"
        except NightscoutConnectionError:
            return {}, False, "cannot_connect"
        return status, client.uses_api_secret, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _normalize_url(user_input[CONF_URL])
            if not urlparse(url).netloc:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url.lower())
                self._abort_if_unique_id_configured()
                credential = user_input.get(CONF_API_KEY) or None
                status, uses_secret, error = await self._validate(url, credential)
                if error:
                    errors["base"] = error
                else:
                    title = (status.get("settings") or {}).get("customTitle") or urlparse(url).netloc
                    return self.async_create_entry(
                        title=title,
                        data={CONF_URL: url, CONF_API_KEY: credential, CONF_USE_API_SECRET: uses_secret},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_URL): str, vol.Optional(CONF_API_KEY): PASSWORD}),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            credential = user_input.get(CONF_API_KEY) or None
            _, uses_secret, error = await self._validate(entry.data[CONF_URL], credential)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: credential, CONF_USE_API_SECRET: uses_secret},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_API_KEY): PASSWORD}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return NightscoutExtendedOptionsFlow()


class NightscoutExtendedOptionsFlow(OptionsFlow):
    """How often to poll."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL, max=900, step=10,
                            unit_of_measurement="s", mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
