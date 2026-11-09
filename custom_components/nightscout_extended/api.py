"""Nightscout API client and parsing.

Kept free of Home Assistant imports so the parsing can be tested on its own.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

    async def _get(self, path: str) -> Any:
        headers, params = self._auth()
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

    async def async_get_data(self) -> NightscoutData:
        props = await self._get(f"/api/v2/properties/{PROPERTIES}")
        if not isinstance(props, dict):
            raise NightscoutConnectionError("Unexpected properties response")
        return parse_properties(props)

    @property
    def uses_api_secret(self) -> bool:
        return self._use_secret

    def set_uses_api_secret(self, value: bool) -> None:
        self._use_secret = value
