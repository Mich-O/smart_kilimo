"""
tests/test_irrigation_engine.py
Unit tests for core.irrigation_engine. Since the engine is pure (no DB, no
HTTP), these tests build plain in-memory model instances directly — no
fixtures, no database needed.
"""
from datetime import date

import pytest

from core.enums import ApiStatus, IrrigationAction
from core.irrigation_engine import evaluate, get_stage_for_age
from core.models import CropProfile, CropStage, FarmPlot
from services.weather import WeatherReading

TODAY = date(2026, 7, 20)


def make_crop_profile() -> CropProfile:
    profile = CropProfile(
        crop_type="MAIZE",
        name="Maize",
        total_season_days=120,
    )
    profile.stages = [
        CropStage(stage_name="Germination", start_day=1, end_day=20, kc_value=0.40,
                   daily_deficit_constant_mm=2.0, deficit_alert_threshold_mm=20.0, is_maturing_stage=False),
        CropStage(stage_name="Vegetative", start_day=21, end_day=55, kc_value=0.80,
                   daily_deficit_constant_mm=4.0, deficit_alert_threshold_mm=40.0, is_maturing_stage=False),
        CropStage(stage_name="Maturing", start_day=101, end_day=120, kc_value=0.35,
                   daily_deficit_constant_mm=1.75, deficit_alert_threshold_mm=50.0, is_maturing_stage=True),
    ]
    return profile


def make_plot(**overrides) -> FarmPlot:
    defaults = dict(
        plot_id=1,
        phone_number="+254700000001",
        plot_label="Maize Eastern",
        region_code="EASTERN",
        crop_type="MAIZE",
        plot_size_acres=0.25,
        planting_date=TODAY,
        crop_age_days=10,
        water_deficit_mm=0.0,
        dry_maturing_override=False,
        alert_active=False,
    )
    defaults.update(overrides)
    return FarmPlot(**defaults)


def success_reading(rainfall_mm: float) -> WeatherReading:
    return WeatherReading(
        region_code="EASTERN", poll_date=TODAY, rainfall_mm=rainfall_mm,
        api_status=ApiStatus.SUCCESS,
    )


def failed_reading() -> WeatherReading:
    return WeatherReading(
        region_code="EASTERN", poll_date=TODAY, rainfall_mm=None,
        api_status=ApiStatus.TIMEOUT, error_detail="boom",
    )


class TestGetStageForAge:
    def test_returns_matching_stage(self):
        profile = make_crop_profile()
        stage = get_stage_for_age(profile, 10)
        assert stage.stage_name == "Germination"

    def test_age_beyond_last_stage_returns_final_stage(self):
        profile = make_crop_profile()
        stage = get_stage_for_age(profile, 500)
        assert stage.stage_name == "Maturing"

    def test_no_stages_raises(self):
        profile = make_crop_profile()
        profile.stages = []
        with pytest.raises(ValueError):
            get_stage_for_age(profile, 5)


class TestEvaluate:
    def test_dry_maturing_override_suppresses(self):
        profile = make_crop_profile()
        stage = profile.stages[0]
        plot = make_plot(dry_maturing_override=True, water_deficit_mm=15.0)
        decision = evaluate(plot, stage, profile, success_reading(0.0), TODAY)

        assert decision.action == IrrigationAction.SUPPRESS
        assert decision.new_water_deficit_mm == 15.0
        assert decision.should_activate_dry_lock is False
        assert decision.alert_message is None

    def test_maturing_stage_suppresses_and_activates_dry_lock(self):
        profile = make_crop_profile()
        stage = profile.stages[2]  # Maturing
        plot = make_plot(water_deficit_mm=5.0)
        decision = evaluate(plot, stage, profile, success_reading(0.0), TODAY)

        assert decision.action == IrrigationAction.SUPPRESS
        assert decision.should_activate_dry_lock is True

    def test_failed_weather_poll_increments_with_zero_delta(self):
        profile = make_crop_profile()
        stage = profile.stages[0]
        plot = make_plot(water_deficit_mm=10.0)
        decision = evaluate(plot, stage, profile, failed_reading(), TODAY)

        assert decision.action == IrrigationAction.INCREMENT
        assert decision.new_water_deficit_mm == 10.0

    def test_significant_rain_resets_deficit(self):
        profile = make_crop_profile()
        stage = profile.stages[0]
        plot = make_plot(water_deficit_mm=25.0)
        decision = evaluate(plot, stage, profile, success_reading(6.0), TODAY)

        assert decision.action == IrrigationAction.RESET
        assert decision.new_water_deficit_mm == 0.0

    def test_deficit_crossing_threshold_triggers_alert(self):
        profile = make_crop_profile()
        stage = profile.stages[1]  # Vegetative, delta 4.0mm, threshold 40mm
        plot = make_plot(water_deficit_mm=37.0)
        decision = evaluate(plot, stage, profile, success_reading(0.0), TODAY)

        assert decision.action == IrrigationAction.ALERT
        assert decision.new_water_deficit_mm == 41.0
        assert decision.alert_message is not None
        assert "Maize" in decision.alert_message
        assert "Vegetative" in decision.alert_message

    def test_normal_day_increments_deficit(self):
        profile = make_crop_profile()
        stage = profile.stages[0]  # Germination, delta 2.0mm, threshold 40mm
        plot = make_plot(water_deficit_mm=5.0)
        decision = evaluate(plot, stage, profile, success_reading(0.0), TODAY)

        assert decision.action == IrrigationAction.INCREMENT
        assert decision.new_water_deficit_mm == 7.0
        assert decision.alert_message is None

    def test_crop_age_always_increments_by_one(self):
        profile = make_crop_profile()
        stage = profile.stages[0]
        plot = make_plot(crop_age_days=9)
        decision = evaluate(plot, stage, profile, success_reading(0.0), TODAY)
        assert decision.new_crop_age_days == 10
