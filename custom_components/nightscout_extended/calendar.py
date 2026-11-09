"""A calendar of Nightscout treatments: meals, doses, notes, and anything logged for a future time."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import NightscoutConnectionError, Treatment
from .coordinator import NightscoutConfigEntry, NightscoutCoordinator
from .entity import device_info

DEFAULT_LENGTH = timedelta(minutes=5)  # treatments are moments; give them a visible length


def to_event(t: Treatment) -> CalendarEvent:
    length = timedelta(minutes=t.duration_min) if t.duration_min and t.duration_min > 0 else DEFAULT_LENGTH
    description = "\n".join(
        line for line in (t.notes, f"Entered by {t.entered_by}" if t.entered_by else None) if line
    ) or None
    return CalendarEvent(
        start=t.time, end=t.time + length, summary=t.summary, description=description, uid=t.uid
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NightscoutConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NightscoutTreatmentsCalendar(entry.runtime_data)])


class NightscoutTreatmentsCalendar(CoordinatorEntity[NightscoutCoordinator], CalendarEntity):
    """Every treatment as an event. Use a calendar trigger to act on one, e.g. a planned meal."""

    _attr_has_entity_name = True
    _attr_translation_key = "treatments"

    def __init__(self, coordinator: NightscoutCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_treatments"
        self._attr_device_info = device_info(coordinator)

    @property
    def event(self) -> CalendarEvent | None:
        """The event happening now, or else the next one."""
        now = dt_util.utcnow()
        for t in self.coordinator.data.treatments:
            ev = to_event(t)
            if ev.end > now:
                return ev
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        # A treatment can start up to a few minutes before the range and still overlap it.
        fetch_start = start_date - DEFAULT_LENGTH
        try:
            treatments = await self.coordinator.client.async_get_treatments(fetch_start, end_date)
        except NightscoutConnectionError:
            treatments = self.coordinator.data.treatments  # fall back to what the last poll saw
        events = [to_event(t) for t in treatments]
        return [e for e in events if e.end > start_date and e.start < end_date]
