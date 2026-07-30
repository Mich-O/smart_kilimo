"""
services/plot_evaluation.py
Shared "evaluate one plot", "process one reminder", and "apply a farmer's
reply to an alert" logic. Extracted out of services.daily_cycle and
services.sms so that the admin operations tools, the SMS callback, and the
farmer web workspace all drive the exact same persistence logic rather
than three separate implementations that could quietly drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from config import settings
from core.constants import compute_water_recommendation
from core.enums import AlertStatus, FarmerReply, IrrigationAction, SMSDirection
from core.irrigation_engine import IrrigationDecision, evaluate, get_stage_for_age
from core.models import AlertRecord, CropProfile, FarmPlot, IrrigationLog, PlotDailyLog, SMSLog
from services.weather import WeatherReading


class SMSSender(Protocol):
    def send(self, phone_number: str, message: str) -> dict:
        ...


@dataclass
class EvaluationOutcome:
    decision: IrrigationDecision
    crop_name: str
    stage_name: str
    threshold_mm: float
    sms_sent: bool


def evaluate_and_apply(
    session, plot: FarmPlot, reading: WeatherReading, sms_client: Optional[SMSSender]
) -> Optional[EvaluationOutcome]:
    """
    Loads the plot's crop profile + current stage, runs the pure engine
    (core.irrigation_engine.evaluate), persists the resulting state onto
    `plot`, creates an AlertRecord and sends SMS when the decision is
    ALERT. Returns None if the plot's crop_type doesn't match any seeded
    CropProfile (a data-integrity issue the caller should log).
    """
    crop_profile = session.get(CropProfile, plot.crop_type)
    if crop_profile is None:
        return None

    stage = get_stage_for_age(crop_profile, plot.crop_age_days)
    decision = evaluate(plot, stage, crop_profile, reading, reading.poll_date)

    plot.water_deficit_mm = decision.new_water_deficit_mm
    plot.crop_age_days = decision.new_crop_age_days
    if decision.should_activate_dry_lock:
        plot.dry_maturing_override = True

    sms_sent = False
    if decision.action == IrrigationAction.RESET:
        plot.alert_active = False
    elif decision.action == IrrigationAction.ALERT:
        plot.alert_active = True
        # Without this check, every single day the deficit stays above
        # threshold creates ANOTHER AlertRecord and sends ANOTHER full
        # alert SMS -- discovered when building the farmer web "mark as
        # watered" action: resolving the most recent of several duplicate
        # ACTIVE alerts left the older ones stuck open underneath it. The
        # reminder pass already exists to nudge the farmer about a
        # standing alert on its own interval; a fresh ALERT should only
        # fire for a genuinely new threshold crossing, not every
        # re-evaluation while one is already open.
        existing_alert = (
            session.query(AlertRecord)
            .filter(AlertRecord.plot_id == plot.plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
            .first()
        )
        if existing_alert is None:
            session.add(
                AlertRecord(
                    plot_id=plot.plot_id,
                    phone_number=plot.phone_number,
                    deficit_at_trigger_mm=decision.new_water_deficit_mm,
                    status=AlertStatus.ACTIVE.value,
                )
            )
            if decision.alert_message and sms_client is not None:
                result = sms_client.send(plot.phone_number, decision.alert_message)
                session.add(
                    SMSLog(
                        phone_number=plot.phone_number,
                        direction=SMSDirection.OUTBOUND.value,
                        message_text=decision.alert_message,
                        delivery_status="SENT" if result.get("success") else "FAILED",
                        message_id=result.get("message_id"),
                    )
                )
            sms_sent = bool(result.get("success"))

    session.add(
        PlotDailyLog(
            plot_id=plot.plot_id,
            log_date=reading.poll_date,
            rainfall_mm=reading.rainfall_mm if reading.rainfall_mm is not None else 0.0,
            water_deficit_mm=decision.new_water_deficit_mm,
            crop_age_days=decision.new_crop_age_days,
            action=decision.action.value,
        )
    )

    return EvaluationOutcome(
        decision=decision,
        crop_name=crop_profile.name,
        stage_name=stage.stage_name,
        threshold_mm=stage.deficit_alert_threshold_mm,
        sms_sent=sms_sent,
    )


@dataclass
class ReminderOutcome:
    sent: bool
    expired: bool
    reminder_count: int
    message: Optional[str] = None

def process_reminder(
    session,
    alert: AlertRecord,
    sms_client: Optional[SMSSender],
    *,
    cutoff: Optional[datetime] = None,
    force: bool = False,
) -> Optional[ReminderOutcome]:
    """
    Evaluates whether `alert` is due for a reminder and, if so, sends one
    and updates its reminder_count / status. `force=True` skips the time
    check entirely (used by the demo to make the reminder ladder visible
    without waiting REMINDER_INTERVAL_HOURS in real time).
    Returns None if the alert isn't ACTIVE or isn't due yet.
    """
    if alert.status != AlertStatus.ACTIVE.value:
        return None

    if not force:
        effective_cutoff = cutoff or (
            datetime.now(timezone.utc) - timedelta(hours=settings.reminder_interval_hours)
        )
        last_touch = alert.last_reminder_at or alert.created_at
        last_touch = (
            last_touch.replace(tzinfo=timezone.utc) if last_touch.tzinfo is None else last_touch
        )
        if last_touch > effective_cutoff:
            return None  # not due yet

    plot = session.get(FarmPlot, alert.plot_id)
    if plot is None:
        return None

    if alert.reminder_count >= settings.max_reminders:
        alert.status = AlertStatus.EXPIRED.value
        return ReminderOutcome(sent=False, expired=True, reminder_count=alert.reminder_count)

    crop_profile = session.get(CropProfile, plot.crop_type)
    stage = get_stage_for_age(crop_profile, plot.crop_age_days)
    recommendation = compute_water_recommendation(plot.water_deficit_mm, plot.plot_size_acres)
    message = (
        f"Reminder: your {crop_profile.name} plot still needs water.\n"
        f"Growth stage: {stage.stage_name}\n"
        f"Apply {recommendation.sms_phrase}.\n"
        "Reply 1 = Done  Reply 2 = Remind me later"
    )
    if sms_client is not None:
        result = sms_client.send(alert.phone_number, message)
        session.add(
            SMSLog(
                phone_number=alert.phone_number,
                direction=SMSDirection.OUTBOUND.value,
                message_text=message,
                delivery_status="SENT" if result.get("success") else "FAILED",
                message_id=result.get("message_id"),
            )
        )

    alert.reminder_count += 1
    alert.last_reminder_at = datetime.now(timezone.utc)

    expired = False
    if alert.reminder_count >= settings.max_reminders:
        alert.status = AlertStatus.EXPIRED.value
        expired = True

    return ReminderOutcome(sent=True, expired=expired, reminder_count=alert.reminder_count, message=message)


def apply_farmer_reply(session, alert: AlertRecord, reply: FarmerReply) -> None:
    """
    Applies a farmer's response to an ACTIVE alert -- shared by the SMS
    callback (services.sms.handle_farmer_sms_reply) and the farmer web
    "mark as watered" / "remind me later" actions, so both channels use
    identical logic and can't silently drift apart.

    Caller is responsible for finding the right AlertRecord first. This
    matters: an SMS reply carries no plot context, only a phone number, so
    the SMS path has to guess "the most recent ACTIVE alert for this
    number" -- ambiguous if a farmer has multiple plots alerting at once.
    The web path knows exactly which plot the farmer is looking at, so it
    can resolve the correct alert precisely. That's a real difference
    between the two channels, not a bug to paper over here.
    """
    plot = session.get(FarmPlot, alert.plot_id)

    session.add(
        IrrigationLog(
            plot_id=alert.plot_id,
            phone_number=alert.phone_number,
            alert_id=alert.alert_id,
            action=reply.value,
            deficit_at_action_mm=plot.water_deficit_mm if plot else alert.deficit_at_trigger_mm,
        )
    )

    if reply == FarmerReply.CONFIRMED:
        alert.status = AlertStatus.RESOLVED.value
        if plot is not None:
            plot.water_deficit_mm = 0.0
            plot.alert_active = False
    elif reply == FarmerReply.DEFERRED:
        # Leave alert ACTIVE; the reminder pass follows up on its own
        # schedule regardless -- this reply's only effect is the audit
        # trail confirming the farmer actually saw the alert.
        pass
