"""
api/routes/admin_web.py
The admin operations dashboard: system overview, farmer/plot directory,
notifications (feed + manual compose), the real production cycle trigger,
and a scenario/testing tool panel. The testing panel reuses
services.plot_evaluation directly (same code the real cycle runs) so it's
a genuine ops tool, not a mock — it just lets an admin supply a chosen
rainfall value or force a reminder instead of waiting on the real clock,
which is useful for support/QA in production and for evaluation here.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from api.forms import ComposeNotificationForm, SimulateDayForm
from core.constants import compute_water_recommendation
from core.enums import AlertStatus, ApiStatus, SMSDirection
from core.irrigation_engine import get_stage_for_age
from core.models import (
    AlertRecord,
    CropProfile,
    Farmer,
    FarmPlot,
    RegionProfile,
    SMSLog,
)
from db.database import get_session
from services.auth import role_required
from services.daily_cycle import run_daily_cycle
from services.plot_evaluation import evaluate_and_apply, process_reminder
from services.weather import WeatherReading

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _sms_client():
    from flask import current_app

    return current_app.config["SMS_CLIENT"]


@admin_bp.route("/dashboard")
@login_required
@role_required("admin")
def dashboard():
    with get_session() as session:
        total_farmers = session.query(Farmer).count()
        total_plots = session.query(FarmPlot).count()
        active_alerts = session.query(AlertRecord).filter(AlertRecord.status == AlertStatus.ACTIVE.value).count()
        needs_water = session.query(FarmPlot).filter(FarmPlot.alert_active == True).count()  # noqa: E712

        by_region = (
            session.query(FarmPlot.region_code, RegionProfile.name)
            .join(RegionProfile, FarmPlot.region_code == RegionProfile.region_code)
            .all()
        )
        region_counts: dict[str, int] = {}
        for code, name in by_region:
            region_counts[name] = region_counts.get(name, 0) + 1

        recent_alerts = (
            session.query(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(8).all()
        )

    return render_template(
        "admin/dashboard.html",
        total_farmers=total_farmers,
        total_plots=total_plots,
        active_alerts=active_alerts,
        needs_water=needs_water,
        region_counts=region_counts,
        recent_alerts=recent_alerts,
    )


@admin_bp.route("/farmers")
@login_required
@role_required("admin")
def farmers():
    query = request.args.get("q", "").strip()
    with get_session() as session:
        q = session.query(Farmer)
        if query:
            q = q.filter(Farmer.phone_number.contains(query))
        farmer_rows = q.order_by(Farmer.created_at.desc()).limit(200).all()

        plot_counts = dict(
            session.query(FarmPlot.phone_number, func.count(FarmPlot.plot_id))
            .group_by(FarmPlot.phone_number)
            .all()
        )
        data = [
            {"farmer": f, "plot_count": plot_counts.get(f.phone_number, 0)}
            for f in farmer_rows
        ]
    return render_template("admin/farmers.html", rows=data, query=query)


@admin_bp.route("/farmers/<phone_number>")
@login_required
@role_required("admin")
def farmer_detail(phone_number: str):
    with get_session() as session:
        farmer = session.get(Farmer, phone_number)
        if farmer is None:
            abort(404)
        plots = (
            session.query(FarmPlot)
            .filter(FarmPlot.phone_number == phone_number)
            .order_by(FarmPlot.created_at.desc())
            .all()
        )
        messages = (
            session.query(SMSLog)
            .filter(SMSLog.phone_number == phone_number)
            .order_by(SMSLog.created_at.desc())
            .limit(30)
            .all()
        )
    return render_template("admin/farmer_detail.html", farmer=farmer, plots=plots, messages=messages)


@admin_bp.route("/plots/<int:plot_id>")
@login_required
@role_required("admin")
def plot_detail(plot_id: int):
    with get_session() as session:
        plot = session.get(FarmPlot, plot_id)
        if plot is None:
            abort(404)
        crop_profile = session.get(CropProfile, plot.crop_type)
        stage = get_stage_for_age(crop_profile, plot.crop_age_days) if crop_profile else None
        active_alert = (
            session.query(AlertRecord)
            .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
            .order_by(AlertRecord.created_at.desc())
            .first()
        )
        recommendation = (
            compute_water_recommendation(plot.water_deficit_mm, plot.plot_size_acres)
            if plot.water_deficit_mm > 0
            else None
        )
    return render_template(
        "admin/plot_detail.html",
        plot=plot,
        crop_profile=crop_profile,
        stage=stage,
        active_alert=active_alert,
        recommendation=recommendation,
    )


@admin_bp.route("/notifications", methods=["GET", "POST"])
@login_required
@role_required("admin")
def notifications():
    with get_session() as session:
        plots = session.query(FarmPlot).order_by(FarmPlot.plot_label).all()
        regions = session.query(RegionProfile).order_by(RegionProfile.name).all()

    form = ComposeNotificationForm()
    form.plot_id.choices = [(p.plot_id, f"{p.plot_label} — {p.phone_number}") for p in plots]
    form.region_code.choices = [(r.region_code, r.name) for r in regions]

    if form.validate_on_submit():
        sms_client = _sms_client()
        sent_count = 0
        with get_session() as session:
            if form.target_type.data == "plot":
                plot = session.get(FarmPlot, form.plot_id.data)
                targets = [plot.phone_number] if plot else []
            else:
                targets = [
                    p.phone_number
                    for p in session.query(FarmPlot).filter(FarmPlot.region_code == form.region_code.data).all()
                ]
                targets = list(dict.fromkeys(targets))  # de-dupe, preserve order

            for phone in targets:
                result = sms_client.send(phone, form.message.data)
                session.add(
                    SMSLog(
                        phone_number=phone,
                        direction=SMSDirection.OUTBOUND.value,
                        message_text=form.message.data[:320],
                        delivery_status="SENT" if result.get("success") else "FAILED",
                        message_id=result.get("message_id"),
                    )
                )
                if result.get("success"):
                    sent_count += 1

        flash(f"Sent to {sent_count} farmer(s).", "success")
        return redirect(url_for("admin.notifications"))

    with get_session() as session:
        feed = session.query(SMSLog).order_by(SMSLog.created_at.desc()).limit(60).all()

    return render_template("admin/notifications.html", form=form, feed=feed)


@admin_bp.route("/cycle", methods=["GET", "POST"])
@login_required
@role_required("admin")
def cycle():
    summary = None
    if request.method == "POST":
        summary = run_daily_cycle(_sms_client())
        flash("Real production cycle complete.", "success")
    return render_template("admin/cycle.html", summary=summary)


@admin_bp.route("/tools", methods=["GET", "POST"])
@login_required
@role_required("admin")
def tools():
    with get_session() as session:
        plots = session.query(FarmPlot).order_by(FarmPlot.plot_label).all()

    form = SimulateDayForm()
    form.plot_id.choices = [(p.plot_id, f"{p.plot_label} — {p.phone_number}") for p in plots]

    outcome = None
    if form.validate_on_submit():
        try:
            rainfall = float(form.rainfall_mm.data)
        except ValueError:
            flash("Rainfall must be a number.", "error")
            return render_template("admin/tools.html", form=form, outcome=None)

        with get_session() as session:
            plot = session.get(FarmPlot, form.plot_id.data)
            if plot is None:
                abort(404)
            reading = WeatherReading(
                region_code=plot.region_code, poll_date=date.today(), rainfall_mm=rainfall, api_status=ApiStatus.SUCCESS
            )
            result = evaluate_and_apply(session, plot, reading, _sms_client())
            if result is not None:
                outcome = {
                    "action": result.decision.action.value,
                    "reason": result.decision.reason,
                    "sms_sent": result.sms_sent,
                    "alert_message": result.decision.alert_message,
                }

    return render_template("admin/tools.html", form=form, outcome=outcome)


@admin_bp.route("/tools/force-reminder/<int:plot_id>", methods=["POST"])
@login_required
@role_required("admin")
def force_reminder(plot_id: int):
    with get_session() as session:
        alert = (
            session.query(AlertRecord)
            .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
            .order_by(AlertRecord.created_at.desc())
            .first()
        )
        if alert is None:
            flash("No active alert on that plot.", "error")
        else:
            outcome = process_reminder(session, alert, _sms_client(), force=True)
            if outcome and outcome.sent:
                flash(f"Reminder #{outcome.reminder_count} sent.", "success")
            else:
                flash("Reminder not sent.", "error")
    return redirect(request.referrer or url_for("admin.tools"))
