"""
tests/fakes/fake_weather.py
Deterministic fake in place of services.weather.fetch_rainfall — no network
calls, fully controllable outcomes for tests.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

from core.enums import ApiStatus
from core.models import RegionProfile
from services.weather import WeatherReading, _yesterday_eat


class FakeWeatherClient:
    """
    Usage:
        fake = FakeWeatherClient()
        fake.set_rainfall("EASTERN", 0.0)
        fake.set_failure("COAST", ApiStatus.TIMEOUT)

        reading = fake.fetch_rainfall(region)
    """

    def __init__(self) -> None:
        self._rainfall: Dict[str, float] = {}
        self._failures: Dict[str, ApiStatus] = {}
        self._poll_date = _yesterday_eat()

    def set_rainfall(self, region_code: str, rainfall_mm: float) -> None:
        self._rainfall[region_code] = rainfall_mm
        self._failures.pop(region_code, None)

    def set_failure(self, region_code: str, status: ApiStatus) -> None:
        self._failures[region_code] = status
        self._rainfall.pop(region_code, None)

    def set_poll_date(self, poll_date: date) -> None:
        self._poll_date = poll_date

    def fetch_rainfall(self, region: RegionProfile) -> WeatherReading:
        code = region.region_code

        if code in self._failures:
            return WeatherReading(
                region_code=code,
                poll_date=self._poll_date,
                rainfall_mm=None,
                api_status=self._failures[code],
                error_detail="Simulated failure",
            )

        rainfall = self._rainfall.get(code, 0.0)
        return WeatherReading(
            region_code=code,
            poll_date=self._poll_date,
            rainfall_mm=rainfall,
            api_status=ApiStatus.SUCCESS,
            error_detail=None,
        )
