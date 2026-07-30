"""
core/irrigation_engine.py
Pure FAO-56 simplified water balance logic. Zero I/O: no DB access, no HTTP,
no side effects. Every function here is a straightforward transformation of
its inputs into outputs, which makes it trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from core.constants import compute_water_recommendation
from core.enums import IrrigationAction
from core.models import CropProfile, CropStage, FarmPlot
from services.weather import WeatherReading


@dataclass(frozen=True)
class IrrigationDecision:
    action: IrrigationAction
    new_water_deficit_mm: float
    new_crop_age_days: int
    should_activate_dry_lock: bool
    alert_message: Optional[str]
    reason: str


ALERT_TEMPLATE = (
    "Smart Kilimo Alert\n"
    "Your {crop} needs water today.\n"
    "Growth stage: {stage}\n"
    "Apply {volume_phrase}.\n"
    "Reply 1 = Done  Reply 2 = Remind me later"
)


def get_stage_for_age(crop_profile: CropProfile, crop_age_days: int) -> CropStage:
    """
    Returns the CropStage whose [start_day, end_day] window contains
    crop_age_days. If the plant has outlived every defined stage (age beyond
    the last stage's end_day), the final stage is returned — this is treated
    as continued maturing, not an error.
    """
    stages = sorted(crop_profile.stages, key=lambda s: s.start_day)
    if not stages:
        raise ValueError(f"CropProfile '{crop_profile.crop_type}' has no stages defined")

    for stage in stages:
        if stage.start_day <= crop_age_days <= stage.end_day:
            return stage

    if crop_age_days < stages[0].start_day:
        return stages[0]

    return stages[-1]


def evaluate(
    farm_plot: FarmPlot,
    stage: CropStage,
    crop_profile: CropProfile,
    weather_reading: WeatherReading,
    today: date,
) -> IrrigationDecision:
    """
    Decision order (first match wins):
      1. farm_plot.dry_maturing_override True     -> SUPPRESS
      2. stage.is_maturing_stage True              -> SUPPRESS + activate dry lock
      3. weather poll failed                       -> INCREMENT with delta=0
      4. rainfall_mm >= reset threshold             -> RESET (deficit = 0)
      5. deficit + daily_delta >= alert threshold   -> ALERT
      6. otherwise                                  -> INCREMENT
    """
    new_age = farm_plot.crop_age_days + 1

    if farm_plot.dry_maturing_override:
        return IrrigationDecision(
            action=IrrigationAction.SUPPRESS,
            new_water_deficit_mm=farm_plot.water_deficit_mm,
            new_crop_age_days=new_age,
            should_activate_dry_lock=False,
            alert_message=None,
            reason="Plot is in dry-maturing override; alerts suppressed.",
        )

    if stage.is_maturing_stage:
        return IrrigationDecision(
            action=IrrigationAction.SUPPRESS,
            new_water_deficit_mm=farm_plot.water_deficit_mm,
            new_crop_age_days=new_age,
            should_activate_dry_lock=True,
            alert_message=None,
            reason=f"Stage '{stage.stage_name}' is a maturing stage; dry lock activated.",
        )

    if not weather_reading.succeeded:
        return IrrigationDecision(
            action=IrrigationAction.INCREMENT,
            new_water_deficit_mm=farm_plot.water_deficit_mm,
            new_crop_age_days=new_age,
            should_activate_dry_lock=False,
            alert_message=None,
            reason=f"Weather poll failed ({weather_reading.api_status.value}); deficit unchanged.",
        )

    if weather_reading.is_significant_rain:
        return IrrigationDecision(
            action=IrrigationAction.RESET,
            new_water_deficit_mm=0.0,
            new_crop_age_days=new_age,
            should_activate_dry_lock=False,
            alert_message=None,
            reason=f"Rainfall {weather_reading.rainfall_mm}mm met reset threshold; deficit reset to 0.",
        )

    projected_deficit = farm_plot.water_deficit_mm + stage.daily_deficit_constant_mm

    if projected_deficit >= stage.deficit_alert_threshold_mm:
        recommendation = compute_water_recommendation(projected_deficit, farm_plot.plot_size_acres)
        message = ALERT_TEMPLATE.format(
            crop=crop_profile.name,
            stage=stage.stage_name,
            volume_phrase=recommendation.sms_phrase,
        )
        return IrrigationDecision(
            action=IrrigationAction.ALERT,
            new_water_deficit_mm=projected_deficit,
            new_crop_age_days=new_age,
            should_activate_dry_lock=False,
            alert_message=message,
            reason=f"Projected deficit {projected_deficit}mm reached alert threshold "
            f"{stage.deficit_alert_threshold_mm}mm.",
        )

    return IrrigationDecision(
        action=IrrigationAction.INCREMENT,
        new_water_deficit_mm=projected_deficit,
        new_crop_age_days=new_age,
        should_activate_dry_lock=False,
        alert_message=None,
        reason=f"Deficit incremented by {stage.daily_deficit_constant_mm}mm to {projected_deficit}mm.",
    )
