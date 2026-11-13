"""Sensors for Nightscout Extended."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfBloodGlucoseConcentration
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.util import dt as dt_util

from .api import NightscoutData

from .const import UNIT_MMOL
from .coordinator import NightscoutConfigEntry, NightscoutCoordinator
from .entity import device_info

MGDL = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER
MMOL = UnitOfBloodGlucoseConcentration.MILLIMOLE_PER_LITER

# Nightscout's trend names (direction plugin) -> HA state keys, which must be snake_case.
DIRECTIONS = {
    "DoubleUp": "double_up",
    "SingleUp": "single_up",
    "FortyFiveUp": "forty_five_up",
    "Flat": "flat",
    "FortyFiveDown": "forty_five_down",
    "SingleDown": "single_down",
    "DoubleDown": "double_down",
    "NOT COMPUTABLE": "not_computable",
    "RATE OUT OF RANGE": "rate_out_of_range",
    "NONE": "none",
}


@dataclass(frozen=True, kw_only=True)
class NightscoutSensorDescription(SensorEntityDescription):
    value_fn: Callable[[NightscoutData], Any]
    attrs_fn: Callable[[NightscoutData], dict[str, Any]] | None = None
    glucose: bool = False  # follows the site's mg/dL or mmol/L setting


def _forecast_attrs(data: NightscoutData) -> dict[str, Any]:
    return {
        "forecast": [
            {"time": p.time.isoformat(), "mgdl": round(p.mgdl)} for p in data.forecast
        ],
        "model": "AR2",
    }


def _last(attr: str) -> Callable[[NightscoutData], float | None]:
    def fn(data: NightscoutData) -> float | None:
        t = data.last_treatment(attr, dt_util.utcnow())
        return getattr(t, attr) if t else None
    return fn


def _last_attrs(attr: str) -> Callable[[NightscoutData], dict[str, Any]]:
    def fn(data: NightscoutData) -> dict[str, Any]:
        t = data.last_treatment(attr, dt_util.utcnow())
        if t is None:
            return {"time": None}
        return {"time": t.time.isoformat(), "event_type": t.event_type, "notes": t.notes}
    return fn


def _forecast(minutes: int) -> Callable[[NightscoutData], float | None]:
    def fn(data: NightscoutData) -> float | None:
        point = data.forecast_at(minutes)
        return point.mgdl if point else None
    return fn


SENSORS: tuple[NightscoutSensorDescription, ...] = (
    NightscoutSensorDescription(
        key="glucose",
        translation_key="glucose",
        glucose=True,
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.glucose,
        attrs_fn=lambda d: {"direction": d.direction, "delta": d.delta},
    ),
    NightscoutSensorDescription(
        key="glucose_delta",
        translation_key="glucose_delta",
        glucose=True,
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.delta,
    ),
    NightscoutSensorDescription(
        key="glucose_trend",
        translation_key="glucose_trend",
        device_class=SensorDeviceClass.ENUM,
        options=list(DIRECTIONS.values()),
        value_fn=lambda d: DIRECTIONS.get(d.direction or ""),
    ),
    NightscoutSensorDescription(
        key="last_reading",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.glucose_time,
    ),
    NightscoutSensorDescription(
        key="iob",
        translation_key="iob",
        native_unit_of_measurement="U",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.iob,
        attrs_fn=lambda d: {"source": d.iob_source},
    ),
    NightscoutSensorDescription(
        key="cob",
        translation_key="cob",
        native_unit_of_measurement="g",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.cob,
        attrs_fn=lambda d: {"source": d.cob_source},
    ),
    NightscoutSensorDescription(
        key="last_carbs",
        translation_key="last_carbs",
        native_unit_of_measurement="g",
        suggested_display_precision=0,
        value_fn=_last("carbs"),
        attrs_fn=_last_attrs("carbs"),
    ),
    NightscoutSensorDescription(
        key="last_insulin",
        translation_key="last_insulin",
        native_unit_of_measurement="U",
        suggested_display_precision=2,
        value_fn=_last("insulin"),
        attrs_fn=_last_attrs("insulin"),
    ),
    NightscoutSensorDescription(
        key="basal",
        translation_key="basal",
        native_unit_of_measurement="U/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.basal,
    ),
    NightscoutSensorDescription(
        key="forecast_15",
        translation_key="forecast_15",
        glucose=True,
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        value_fn=_forecast(15),
    ),
    NightscoutSensorDescription(
        key="forecast_30",
        translation_key="forecast_30",
        glucose=True,
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        value_fn=_forecast(30),
        attrs_fn=_forecast_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NightscoutConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(NightscoutSensor(coordinator, description) for description in SENSORS)


class NightscoutSensor(CoordinatorEntity[NightscoutCoordinator], SensorEntity):
    """One Nightscout value."""

    _attr_has_entity_name = True
    entity_description: NightscoutSensorDescription

    def __init__(self, coordinator: NightscoutCoordinator, description: NightscoutSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{description.key}"
        if description.glucose:
            # Nightscout reports mg/dL; show the site's own unit, HA converts.
            self._attr_native_unit_of_measurement = MGDL
            self._attr_suggested_unit_of_measurement = MMOL if coordinator.units == UNIT_MMOL else MGDL
            self._attr_suggested_display_precision = 1 if coordinator.units == UNIT_MMOL else 0
        self._attr_device_info = device_info(coordinator)

    @property
    def native_value(self) -> float | int | str | datetime | None:
        value = self.entity_description.value_fn(self.coordinator.data)
        if isinstance(value, float) and value.is_integer():
            return int(value)  # Nightscout's mg/dL values are whole numbers; avoid "155.0"
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs = (
            self.entity_description.attrs_fn(self.coordinator.data)
            if self.entity_description.attrs_fn
            else None
        )
        if self.entity_description.key == "glucose":
            # For the bundled card: colour the reading by the site's own alarm levels.
            attrs = {**(attrs or {}), "thresholds_mgdl": self.coordinator.thresholds_mgdl}
        return attrs
