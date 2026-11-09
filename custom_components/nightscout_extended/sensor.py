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
from homeassistant.const import CONF_URL, UnitOfBloodGlucoseConcentration
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NightscoutData
from .const import DOMAIN, UNIT_MMOL
from .coordinator import NightscoutConfigEntry, NightscoutCoordinator

MGDL = UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER
MMOL = UnitOfBloodGlucoseConcentration.MILLIMOLE_PER_LITER

# Nightscout's trend names (direction plugin)
DIRECTIONS = [
    "DoubleUp", "SingleUp", "FortyFiveUp", "Flat", "FortyFiveDown", "SingleDown",
    "DoubleDown", "NOT COMPUTABLE", "RATE OUT OF RANGE", "NONE",
]


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
        options=DIRECTIONS,
        value_fn=lambda d: d.direction if d.direction in DIRECTIONS else None,
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="Nightscout",
            sw_version=coordinator.status.get("version"),
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data[CONF_URL],
        )

    @property
    def native_value(self) -> float | int | str | datetime | None:
        value = self.entity_description.value_fn(self.coordinator.data)
        if isinstance(value, float) and value.is_integer():
            return int(value)  # Nightscout's mg/dL values are whole numbers; avoid "155.0"
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
