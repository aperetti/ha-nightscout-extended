"""Runs the integration inside Home Assistant's test harness against a fake Nightscout."""

import json
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nightscout_extended.const import CONF_USE_API_SECRET, DOMAIN

URL = "https://ns.example.com"
PROPS = json.loads((Path(__file__).parent.parent / "tests" / "fixtures" / "properties.json").read_text())
STATUS = {"version": "15.0.9", "settings": {"units": "mg/dl", "customTitle": "Example NS"}}


def _mock(aioclient_mock: AiohttpClientMocker, status=STATUS, ok=True):
    code = 200 if ok else 401
    aioclient_mock.get(f"{URL}/api/v1/status.json", json=status if ok else {}, status=code)
    aioclient_mock.get(f"{URL}/api/v2/properties/bgnow", json=PROPS if ok else {}, status=code)
    aioclient_mock.get(
        f"{URL}/api/v2/properties/bgnow,delta,direction,iob,cob,ar2,basal", json=PROPS if ok else {}, status=code
    )


async def test_config_flow_creates_entry(hass: HomeAssistant, aioclient_mock):
    _mock(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ns.example.com/", CONF_API_KEY: "readable-abc"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example NS"
    assert result["data"] == {CONF_URL: URL, CONF_API_KEY: "readable-abc", CONF_USE_API_SECRET: False}


async def test_config_flow_bad_auth(hass: HomeAssistant, aioclient_mock):
    _mock(aioclient_mock, ok=False)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_API_KEY: "nope"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(("units", "glucose", "unit"), [("mg/dl", "155", "mg/dL"), ("mmol", "8.6", "mmol/L")])
async def test_sensors(hass: HomeAssistant, aioclient_mock, units, glucose, unit):
    _mock(aioclient_mock, status={"version": "15.0.9", "settings": {"units": units}})
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=URL, title="NS",
        data={CONF_URL: URL, CONF_API_KEY: "readable-abc", CONF_USE_API_SECRET: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    g = hass.states.get("sensor.ns_glucose")
    assert round(float(g.state), 1) == float(glucose) and g.attributes["unit_of_measurement"] == unit
    assert hass.states.get("sensor.ns_insulin_on_board").state == "1.35"
    assert round(float(hass.states.get("sensor.ns_carbs_on_board").state), 1) == 28.9
    assert hass.states.get("sensor.ns_glucose_trend").state == "FortyFiveUp"
    f30 = hass.states.get("sensor.ns_forecast_in_30_minutes")
    assert len(f30.attributes["forecast"]) == 6
    assert hass.states.get("sensor.ns_last_reading").state == "2026-01-01T13:00:00+00:00"
    assert hass.states.get("sensor.ns_current_basal") is None  # disabled by default

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_have_no_credentials_url_or_health_values(hass: HomeAssistant, aioclient_mock):
    from custom_components.nightscout_extended.diagnostics import async_get_config_entry_diagnostics

    _mock(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=URL, title="NS",
        data={CONF_URL: URL, CONF_API_KEY: "readable-abc", CONF_USE_API_SECRET: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for leaked in ("readable-abc", "ns.example.com", "155", "1.35", "28.9", "Example NS"):
        assert leaked not in diag, leaked
