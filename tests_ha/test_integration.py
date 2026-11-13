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
TREATMENTS = json.loads((Path(__file__).parent.parent / "tests" / "fixtures" / "treatments.json").read_text())
PROPS = json.loads((Path(__file__).parent.parent / "tests" / "fixtures" / "properties.json").read_text())
STATUS = {"version": "15.0.9", "settings": {"units": "mg/dl", "customTitle": "Example NS"}}


def _mock(aioclient_mock: AiohttpClientMocker, status=STATUS, ok=True):
    code = 200 if ok else 401
    aioclient_mock.get(f"{URL}/api/v1/status.json", json=status if ok else {}, status=code)
    aioclient_mock.get(f"{URL}/api/v2/properties/bgnow", json=PROPS if ok else {}, status=code)
    aioclient_mock.get(
        f"{URL}/api/v2/properties/bgnow,delta,direction,iob,cob,ar2,basal", json=PROPS if ok else {}, status=code
    )
    aioclient_mock.get(f"{URL}/api/v1/treatments.json", json=TREATMENTS if ok else [], status=code)


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
    assert hass.states.get("sensor.ns_glucose_trend").state == "forty_five_up"
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


async def _setup(hass, aioclient_mock):
    _mock(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=URL, title="NS",
        data={CONF_URL: URL, CONF_API_KEY: "readable-abc", CONF_USE_API_SECRET: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_last_treatment_sensors_and_calendar(hass: HomeAssistant, aioclient_mock, freezer):
    freezer.move_to("2026-01-01T17:00:00+00:00")
    await _setup(hass, aioclient_mock)

    carbs = hass.states.get("sensor.ns_last_carbs")
    assert carbs.state == "30" and carbs.attributes["time"].startswith("2026-01-01T12:00:00")
    assert hass.states.get("sensor.ns_last_insulin").state == "0.5"  # the 15:30 correction

    cal = hass.states.get("calendar.ns_treatments")
    assert cal.state == "off"  # nothing happening at 17:00
    assert cal.attributes["message"].startswith("Planned meal: Dinner")  # the next event

    resp = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": "calendar.ns_treatments", "start_date_time": "2026-01-01T00:00:00+00:00",
         "end_date_time": "2026-01-02T00:00:00+00:00"},
        blocking=True, return_response=True,
    )
    summaries = [e["summary"] for e in resp["calendar.ns_treatments"]["events"]]
    assert summaries == [
        "Meal Bolus: 30 g carbs", "Meal Bolus: 2.5 U insulin", "Correction Bolus: 0.5 U insulin",
        "Planned meal: Dinner · 45 g carbs, not eaten yet", "Temporary Target",
    ]


@pytest.mark.parametrize("expected_lingering_timers", [True])  # the calendar trigger's own 15-min refresh
async def test_calendar_trigger_fires_before_planned_meal(hass: HomeAssistant, aioclient_mock, freezer):
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service
    from homeassistant.util import dt as dt_util

    freezer.move_to("2026-01-01T17:00:00+00:00")
    await _setup(hass, aioclient_mock)
    calls = async_mock_service(hass, "test", "meal_alarm")
    assert await async_setup_component(hass, "automation", {"automation": {
        "triggers": [{"trigger": "calendar", "event": "start", "entity_id": "calendar.ns_treatments",
                      "offset": "-0:10:00"}],
        "conditions": [{"condition": "template",
                        "value_template": "{{ trigger.calendar_event.summary.startswith('Planned meal') }}"}],
        "actions": [{"action": "test.meal_alarm", "data": {"message": "{{ trigger.calendar_event.summary }}"}}],
    }})
    await hass.async_block_till_done()

    for minute in ("17:20", "17:40", "17:49"):
        freezer.move_to(f"2026-01-01T{minute}:00+00:00"); async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert calls == []  # not yet

    freezer.move_to("2026-01-01T17:50:05+00:00"); async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["message"].startswith("Planned meal: Dinner")


async def _setup(hass: HomeAssistant, aioclient_mock, status=STATUS):
    _mock(aioclient_mock, status=status)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=URL, title="NS",
        data={CONF_URL: URL, CONF_API_KEY: "readable-abc", CONF_USE_API_SECRET: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_glucose_sensor_exposes_site_thresholds(hass: HomeAssistant, aioclient_mock):
    status = {"version": "15.0.9", "settings": {
        "units": "mg/dl",
        "thresholds": {"bgHigh": 250, "bgTargetTop": 170, "bgTargetBottom": 75, "bgLow": "bad"},
    }}
    await _setup(hass, aioclient_mock, status)
    attrs = hass.states.get("sensor.ns_glucose").attributes
    # bgLow is unusable, so Nightscout's default fills that one in.
    assert attrs["thresholds_mgdl"] == {"urgent_low": 55.0, "low": 75.0, "high": 170.0, "urgent_high": 250.0}
    assert attrs["direction"] == "FortyFiveUp"  # existing attributes are kept


async def test_glucose_thresholds_default_when_status_has_none(hass: HomeAssistant, aioclient_mock):
    await _setup(hass, aioclient_mock)
    assert hass.states.get("sensor.ns_glucose").attributes["thresholds_mgdl"] == {
        "urgent_low": 55.0, "low": 80.0, "high": 180.0, "urgent_high": 260.0
    }


async def test_card_is_served_and_loaded_on_dashboards(hass: HomeAssistant, hass_client, aioclient_mock, monkeypatch):
    from homeassistant.setup import async_setup_component

    from custom_components.nightscout_extended import card

    added: list[str] = []
    monkeypatch.setattr(card, "add_extra_js_url", lambda _hass, url: added.append(url))
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")  # the test harness has no frontend package
    await _setup(hass, aioclient_mock)

    assert added == [f"{card.CARD_URL}?v=0.3.0"]
    client = await hass_client()
    resp = await client.get(card.CARD_URL)
    assert resp.status == 200
    assert 'customElements.define("nightscout-glucose-card"' in await resp.text()


async def test_card_skipped_without_frontend(hass: HomeAssistant, aioclient_mock, monkeypatch):
    from custom_components.nightscout_extended import card

    added: list[str] = []
    monkeypatch.setattr(card, "add_extra_js_url", lambda _hass, url: added.append(url))
    entry = await _setup(hass, aioclient_mock)
    assert added == []  # no crash, and the sensors still load
    assert hass.states.get("sensor.ns_glucose") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
