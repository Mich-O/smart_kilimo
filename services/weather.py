"""
services/weather.py
Open-Meteo client. Never raises: every failure mode (timeout, HTTP error,
parse error) is captured in the returned WeatherReading with an appropriate
ApiStatus, so callers never need a try/except around fetch_rainfall().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from config import settings
from core.enums import ApiStatus
from core.models import RegionProfile

REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class WeatherReading:
    region_code: str
    poll_date: date
    rainfall_mm: Optional[float]
    api_status: ApiStatus
    error_detail: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.api_status == ApiStatus.SUCCESS and self.rainfall_mm is not None

    @property
    def is_significant_rain(self) -> bool:
        if not self.succeeded:
            return False
        return self.rainfall_mm >= settings.rainfall_reset_threshold_mm


def _yesterday_eat() -> date:
    """
    Returns yesterday's date in the configured weather timezone. Uses
    zoneinfo rather than naive UTC arithmetic so the result is correct
    regardless of what timezone the server itself runs in. Kenya does not
    observe DST, but zoneinfo is used anyway for correctness and
    configurability (e.g. if the region set ever expands beyond Kenya).
    """
    now_local = datetime.now(ZoneInfo(settings.weather_timezone))
    return (now_local - timedelta(days=1)).date()


def fetch_rainfall(region: RegionProfile) -> WeatherReading:
    """
    Fetches yesterday's confirmed rainfall total for a region from Open-Meteo.
    Uses past_days=1, forecast_days=0 so we always get a *confirmed* total,
    never a forecast estimate. Never raises.
    """
    poll_date = _yesterday_eat()

    params = {
        "latitude": region.latitude,
        "longitude": region.longitude,
        "daily": "precipitation_sum",
        "timezone": settings.weather_timezone,
        "past_days": settings.weather_past_days,
        "forecast_days": 0,
    }

    try:
        response = requests.get(
            settings.weather_api_base_url, params=params, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.Timeout:
        return WeatherReading(
            region_code=region.region_code,
            poll_date=poll_date,
            rainfall_mm=None,
            api_status=ApiStatus.TIMEOUT,
            error_detail=f"Request to Open-Meteo timed out after {REQUEST_TIMEOUT_SECONDS}s",
        )
    except requests.RequestException as exc:
        return WeatherReading(
            region_code=region.region_code,
            poll_date=poll_date,
            rainfall_mm=None,
            api_status=ApiStatus.HTTP_ERROR,
            error_detail=str(exc),
        )

    if response.status_code != 200:
        return WeatherReading(
            region_code=region.region_code,
            poll_date=poll_date,
            rainfall_mm=None,
            api_status=ApiStatus.HTTP_ERROR,
            error_detail=f"Open-Meteo returned HTTP {response.status_code}: {response.text[:200]}",
        )

    try:
        payload = response.json()
        daily = payload["daily"]
        dates = daily["time"]
        totals = daily["precipitation_sum"]

        target_index = dates.index(poll_date.isoformat())
        rainfall_mm = float(totals[target_index])
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        return WeatherReading(
            region_code=region.region_code,
            poll_date=poll_date,
            rainfall_mm=None,
            api_status=ApiStatus.PARSE_ERROR,
            error_detail=f"Could not parse Open-Meteo response: {exc}",
        )

    return WeatherReading(
        region_code=region.region_code,
        poll_date=poll_date,
        rainfall_mm=rainfall_mm,
        api_status=ApiStatus.SUCCESS,
        error_detail=None,
    )
