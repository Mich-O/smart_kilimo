"""
services/daily_cycle.py
Daily orchestrator. For each active region: poll weather once (idempotent),
then evaluate every plot in that region and apply the resulting decision.
Also runs a reminder pass over ACTIVE alerts. Atomic per plot: one plot's
failure is logged and skipped, it never blocks the rest of the cycle.

The actual per-plot evaluation and per-alert reminder logic lives in
services.plot_evaluation, shared with the demo control panel.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from config import settings
from core.enums import AlertStatus, ApiStatus, IrrigationAction
from core.models import AlertRecord, FarmPlot, RegionProfile, WeatherRecord
from db.database import get_session
from services.plot_evaluation import evaluate_and_apply, process_reminder
from services.weather import WeatherReading, fetch_rainfall, _yesterday_eat

logger = logging.getLogger(__name__)


class SMSClient(Protocol):
    def send(self, phone_number: str, message: str) -> dict:
        ...


@dataclass
class CycleSummary:
    total_plots: int = 0
    total_alerts: int = 0
    total_resets: int = 0
    total_suppressed: int = 0
    total_reminders: int = 0
    total_errors: int = 0
    duration_seconds: float = 0.0


def run_daily_cycle(sms_client: SMSClient) -> CycleSummary:
    started = time.monotonic()
    summary = CycleSummary()

    _run_evaluation_pass(sms_client, summary)
    _run_reminder_pass(sms_client, summary)

    summary.duration_seconds = round(time.monotonic() - started, 3)
    logger.info(
        "Daily cycle complete: plots=%s alerts=%s resets=%s suppressed=%s "
        "reminders=%s errors=%s duration=%.3fs",
        summary.total_plots,
        summary.total_alerts,
        summary.total_resets,
        summary.total_suppressed,
        summary.total_reminders,
        summary.total_errors,
        summary.duration_seconds,
    )
    return summary


def _run_evaluation_pass(sms_client: SMSClient, summary: CycleSummary) -> None:
    with get_session() as session:
        region_codes = [
            row[0]
            for row in session.query(FarmPlot.region_code).distinct().all()
        ]

    for region_code in region_codes:
        reading = _get_or_fetch_weather(region_code)

        with get_session() as session:
            plot_ids = [
                row[0]
                for row in session.query(FarmPlot.plot_id)
                .filter(FarmPlot.region_code == region_code)
                .all()
            ]

        for plot_id in plot_ids:
            summary.total_plots += 1
            try:
                _evaluate_single_plot(plot_id, reading, sms_client, summary)
            except Exception:
                summary.total_errors += 1
                logger.exception("Failed to evaluate plot_id=%s", plot_id)


def _get_or_fetch_weather(region_code: str) -> WeatherReading:
    """Idempotent: reuses today's WeatherRecord if one already exists for this region."""
    today = _yesterday_eat()  # the "poll date" the whole cycle keys off of

    with get_session() as session:
        existing = (
            session.query(WeatherRecord)
            .filter(WeatherRecord.region_code == region_code, WeatherRecord.poll_date == today)
            .first()
        )
        if existing is not None:
            return WeatherReading(
                region_code=existing.region_code,
                poll_date=existing.poll_date,
                rainfall_mm=existing.rainfall_mm,
                api_status=ApiStatus(existing.api_status),
                error_detail=existing.error_detail,
            )

        region = session.get(RegionProfile, region_code)
        if region is None:
            logger.error("Region '%s' has plots but no RegionProfile row", region_code)
            return WeatherReading(
                region_code=region_code,
                poll_date=today,
                rainfall_mm=None,
                api_status=ApiStatus.HTTP_ERROR,
                error_detail="RegionProfile not found",
            )

        reading = fetch_rainfall(region)

        session.add(
            WeatherRecord(
                region_code=reading.region_code,
                poll_date=reading.poll_date,
                rainfall_mm=reading.rainfall_mm,
                api_status=reading.api_status.value,
                error_detail=reading.error_detail,
            )
        )
        return reading


def _evaluate_single_plot(
    plot_id: int, reading: WeatherReading, sms_client: SMSClient, summary: CycleSummary
) -> None:
    with get_session() as session:
        plot = session.get(FarmPlot, plot_id)
        if plot is None:
            return

        outcome = evaluate_and_apply(session, plot, reading, sms_client)
        if outcome is None:
            logger.error("Plot %s references unknown crop_type '%s'", plot_id, plot.crop_type)
            return

        action = outcome.decision.action
        if action == IrrigationAction.RESET:
            summary.total_resets += 1
        elif action == IrrigationAction.SUPPRESS:
            summary.total_suppressed += 1
        elif action == IrrigationAction.ALERT:
            summary.total_alerts += 1


def _run_reminder_pass(sms_client: SMSClient, summary: CycleSummary) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.reminder_interval_hours)

    with get_session() as session:
        active_alert_ids = [
            row[0]
            for row in session.query(AlertRecord.alert_id)
            .filter(AlertRecord.status == AlertStatus.ACTIVE.value)
            .all()
        ]

    for alert_id in active_alert_ids:
        try:
            _process_reminder(alert_id, cutoff, sms_client, summary)
        except Exception:
            summary.total_errors += 1
            logger.exception("Failed to process reminder for alert_id=%s", alert_id)


def _process_reminder(
    alert_id: int, cutoff: datetime, sms_client: SMSClient, summary: CycleSummary
) -> None:
    with get_session() as session:
        alert = session.get(AlertRecord, alert_id)
        if alert is None:
            return
        outcome = process_reminder(session, alert, sms_client, cutoff=cutoff)
        if outcome is not None and outcome.sent:
            summary.total_reminders += 1
