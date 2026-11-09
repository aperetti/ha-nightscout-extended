"""Nightscout API client and parsing.

Kept free of Home Assistant imports so the parsing can be tested on its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

MMOL_PER_MGDL = 1 / 18.0182

PROPERTIES = "bgnow,delta,direction,iob,cob,ar2,basal"


class NightscoutAuthError(Exception):
    """The URL answered but rejected the credentials."""


class NightscoutConnectionError(Exception):
    """Nightscout could not be reached or answered with something unexpected."""


@dataclass
class ForecastPoint:
    time: datetime
    mgdl: float


@dataclass
class Treatment:
    """A Nightscout treatment: a meal, a dose, a note, or anything else Care Portal can log."""

    uid: str
    time: datetime
    event_type: str
    carbs: float | None = None
    insulin: float | None = None
    notes: str | None = None
    entered_by: str | None = None
    duration_min: float | None = None

    @property
    def summary(self) -> str:
        parts = []
        if self.carbs:
            parts.append(f"{self.carbs:g} g carbs")
        if self.insulin:
            parts.append(f"{self.insulin:g} U insulin")
        if parts:
            return f"{self.event_type}: {', '.join(parts)}"
        if self.notes:
            return self.notes.strip().splitlines()[0][:120]
        return self.event_type


@dataclass
class NightscoutData:
    """One poll's worth of values. Glucose values are always mg/dL here."""

    glucose: float | None = None
    glucose_time: datetime | None = None
    delta: float | None = None
    direction: str | None = None
    iob: float | None = None
    iob_source: str | None = None
    cob: float | None = None
    cob_source: str | None = None
    basal: float | None = None
    forecast: list[ForecastPoint] = field(default_factory=list)
    treatments: list[Treatment] = field(default_factory=list)  # sorted by time, past and future

    def last_treatment(self, attr: str, now: datetime) -> Treatment | None:
        """The most recent treatment at or before `now` with a non-zero `attr` (carbs or insulin)."""
        for t in reversed(self.treatments):
            if t.time <= now and getattr(t, attr):
                return t
        return None

    def forecast_at(self, minutes: int) -> ForecastPoint | None:
        """The forecast point closest to `minutes` after the latest reading (within 3 min)."""
        if not self.forecast or self.glucose_time is None:
            return None
        target = self.glucose_time.timestamp() + minutes * 60
        best = min(self.forecast, key=lambda p: abs(p.time.timestamp() - target))
        return best if abs(best.time.timestamp() - target) <= 180 else None


def _ms_to_dt(ms: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_properties(props: dict[str, Any]) -> NightscoutData:
    """Turn a /api/v2/properties response into NightscoutData.

    Every plugin is optional: a Nightscout without `cob` enabled simply leaves cob as None.
    """
    data = NightscoutData()

    bgnow = props.get("bgnow") or {}
    data.glucose = _num(bgnow.get("last"))
    data.glucose_time = _ms_to_dt(bgnow.get("mills"))

    delta = props.get("delta") or {}
    data.delta = _num(delta.get("mgdl"))

    direction = props.get("direction") or {}
    data.direction = direction.get("value") or None

    iob = props.get("iob") or {}
    data.iob = _num(iob.get("iob"))
    data.iob_source = iob.get("source")

    cob = props.get("cob") or {}
    data.cob = _num(cob.get("cob"))
    data.cob_source = cob.get("source")

    basal = (props.get("basal") or {}).get("current") or {}
    data.basal = _num(basal.get("totalbasal"))

    predicted = ((props.get("ar2") or {}).get("forecast") or {}).get("predicted") or []
    for point in predicted:
        when, mgdl = _ms_to_dt(point.get("mills")), _num(point.get("mgdl"))
        if when is not None and mgdl is not None:
            data.forecast.append(ForecastPoint(when, mgdl))
    data.forecast.sort(key=lambda p: p.time)
    return data


def _parse_time(item: dict[str, Any]) -> datetime | None:
    created = item.get("created_at")
    if isinstance(created, str) and created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return _ms_to_dt(item.get("mills") or item.get("date"))


def parse_treatments(items: Any) -> list[Treatment]:
    """Turn /api/v1/treatments.json into Treatments, oldest first. Unusable entries are skipped."""
    out: list[Treatment] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        when = _parse_time(item)
        if when is None:
            continue
        notes = item.get("notes")
        out.append(
            Treatment(
                uid=str(item.get("_id") or item.get("identifier") or f"{item.get('eventType')}-{when.isoformat()}"),
                time=when,
                event_type=str(item.get("eventType") or "Treatment"),
                carbs=_num(item.get("carbs")) or None,
                insulin=_num(item.get("insulin")) or None,
                notes=notes if isinstance(notes, str) and notes.strip() else None,
                entered_by=item.get("enteredBy") if isinstance(item.get("enteredBy"), str) else None,
                duration_min=_num(item.get("duration")) or None,
            )
        )
    out.sort(key=lambda t: t.time)
    return out


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class NightscoutClient:
    """Reads a Nightscout site with an access token or the API secret."""

    def __init__(self, session: aiohttp.ClientSession, url: str, credential: str | None) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._credential = (credential or "").strip() or None
        self._use_secret = False

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        if not self._credential:
            return {}, {}
        if self._use_secret:
            digest = hashlib.sha1(self._credential.encode()).hexdigest()
            return {"api-secret": digest}, {}
        return {}, {"token": self._credential}

    async def _get(self, path: str, query: dict[str, str] | None = None) -> Any:
        headers, params = self._auth()
        params = {**(query or {}), **params}
        headers["Accept"] = "application/json"
        try:
            # No redirects: the token or API secret must never be sent to a host the user didn't enter.
            async with self._session.get(
                f"{self.url}{path}", headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False,
            ) as resp:
                if resp.status in (401, 403):
                    raise NightscoutAuthError(f"HTTP {resp.status}")
                if 300 <= resp.status < 400:
                    target = resp.headers.get("Location", "another address")
                    raise NightscoutConnectionError(
                        f"Nightscout redirected to {self._redact(target)}; use that URL instead"
                    )
                if resp.status != 200:
                    raise NightscoutConnectionError(f"HTTP {resp.status} from {path}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            # aiohttp messages can include the request URL, which carries the token.
            raise NightscoutConnectionError(self._redact(f"{type(err).__name__}: {err}")) from None
        except ValueError:
            raise NightscoutConnectionError(f"Not JSON from {path}") from None

    def _redact(self, text: str) -> str:
        """Remove the credential (and its SHA-1) from text that may be logged."""
        if self._credential:
            text = text.replace(self._credential, "**REDACTED**")
            text = text.replace(hashlib.sha1(self._credential.encode()).hexdigest(), "**REDACTED**")
        return text

    async def async_get_status(self) -> dict[str, Any]:
        """Check the URL and credential. Tries the credential as a token, then as the API secret."""
        try:
            status = await self._get("/api/v1/status.json")
        except NightscoutAuthError:
            if not self._credential or self._use_secret:
                raise
            self._use_secret = True
            status = await self._get("/api/v1/status.json")
        if not isinstance(status, dict) or "settings" not in status:
            raise NightscoutConnectionError("This doesn't look like a Nightscout site")
        # Older Nightscout returns status without auth, so prove the credential on a protected call.
        await self._get("/api/v2/properties/bgnow")
        return status

    async def async_get_treatments(self, start: datetime, end: datetime, limit: int = 1000) -> list[Treatment]:
        items = await self._get(
            "/api/v1/treatments.json",
            {
                "find[created_at][$gte]": _iso(start),
                "find[created_at][$lte]": _iso(end),
                "count": str(limit),
            },
        )
        return parse_treatments(items)

    async def async_get_data(
        self, past: timedelta = timedelta(hours=48), ahead: timedelta = timedelta(days=62)
    ) -> NightscoutData:
        props = await self._get(f"/api/v2/properties/{PROPERTIES}")
        if not isinstance(props, dict):
            raise NightscoutConnectionError("Unexpected properties response")
        data = parse_properties(props)
        now = datetime.now(timezone.utc)
        data.treatments = await self.async_get_treatments(now - past, now + ahead)
        return data

    @property
    def uses_api_secret(self) -> bool:
        return self._use_secret

    def set_uses_api_secret(self, value: bool) -> None:
        self._use_secret = value
