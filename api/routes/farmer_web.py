"""
api/routes/farmer_web.py
The farmer-facing web workspace: dashboard, plot registration, plot detail
with history, and notifications. This is the web equivalent of the USSD
flow in core/ussd_session.py — same business rules (one plot per
crop+region, FAO-56 engine underneath), different channel.

Every route here enforces object-level authorization: a logged-in farmer
can only ever see plots and messages tied to their own phone number, even
if they guess another plot's id in the URL.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from api.forms import PlotRegisterForm, PlotSizeUpdateForm
from core.constants import compute_water_recommendation
from core.enums import AlertStatus, FarmerReply
from core.irrigation_engine import get_stage_for_age
from core.models import AlertRecord, CropProfile, FarmPlot, PlotDailyLog, RegionProfile, SMSLog
from db.database import get_session
from services.auth import role_required
from services.plot_evaluation import apply_farmer_reply

farmer_bp = Blueprint("farmer", __name__, url_prefix="/farmer")


def _region_crop_choices(session):
    regions = [(r.region_code, r.name) for r in session.query(RegionProfile).order_by(RegionProfile.name)]
    crops = [(c.crop_type, c.name) for c in session.query(CropProfile).order_by(CropProfile.name)]
    return regions, crops


def _plot_card(session, plot: FarmPlot) -> dict:
    crop_profile = session.get(CropProfile, plot.crop_type)
    stage = get_stage_for_age(crop_profile, plot.crop_age_days) if crop_profile else None
    return {
        "plot": plot,
        "crop_name": crop_profile.name if crop_profile else plot.crop_type,
        "threshold_mm": stage.deficit_alert_threshold_mm if stage else None,
        "stage_name": stage.stage_name if stage else "—",
    }


@farmer_bp.route("/dashboard")
@login_required
@role_required("farmer")
def dashboard():
    with get_session() as session:
        plots = (
            session.query(FarmPlot)
            .filter(FarmPlot.phone_number == current_user.raw.phone_number)
            .order_by(FarmPlot.created_at.desc())
            .all()
        )
        cards = [_plot_card(session, p) for p in plots]
    return render_template("farmer/dashboard.html", cards=cards)


@farmer_bp.route("/plots/new", methods=["GET", "POST"])
@login_required
@role_required("farmer")
def new_plot():
    with get_session() as session:
        regions, crops = _region_crop_choices(session)

    form = PlotRegisterForm()
    form.region_code.choices = regions
    form.crop_type.choices = crops

    if form.validate_on_submit():
        with get_session() as session:
            region = session.get(RegionProfile, form.region_code.data)
            crop = session.get(CropProfile, form.crop_type.data)

            existing = (
                session.query(FarmPlot)
                .filter(
                    FarmPlot.phone_number == current_user.raw.phone_number,
                    FarmPlot.crop_type == form.crop_type.data,
                    FarmPlot.region_code == form.region_code.data,
                )
                .first()
            )
            if existing is not None:
                flash("You already have a plot with that crop and region.", "error")
                return render_template("farmer/plot_new.html", form=form)

            from datetime import date

            plot = FarmPlot(
                phone_number=current_user.raw.phone_number,
                plot_label=f"{crop.name} {region.name}",
                region_code=form.region_code.data,
                crop_type=form.crop_type.data,
                plot_size_acres=float(form.plot_size_acres.data),
                planting_date=date.today(),
                crop_age_days=0,
                water_deficit_mm=0.0,
                dry_maturing_override=False,
                alert_active=False,
            )
            session.add(plot)
            session.flush()
            plot_id = plot.plot_id

        flash("Plot registered. You'll get an SMS when it needs water.", "success")
        return redirect(url_for("farmer.plot_detail", plot_id=plot_id))

    return render_template("farmer/plot_new.html", form=form)


def _get_owned_plot_or_404(session, plot_id: int) -> FarmPlot:
    plot = session.get(FarmPlot, plot_id)
    if plot is None or plot.phone_number != current_user.raw.phone_number:
        abort(404)
    return plot


@farmer_bp.route("/plots/<int:plot_id>")
@login_required
@role_required("farmer")
def plot_detail(plot_id: int):
    with get_session() as session:
        plot = _get_owned_plot_or_404(session, plot_id)
        crop_profile = session.get(CropProfile, plot.crop_type)
        stage = get_stage_for_age(crop_profile, plot.crop_age_days) if crop_profile else None

        history = (
            session.query(PlotDailyLog)
            .filter(PlotDailyLog.plot_id == plot_id)
            .order_by(PlotDailyLog.log_date.asc())
            .all()
        )
        active_alert = (
            session.query(AlertRecord)
            .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
            .order_by(AlertRecord.created_at.desc())
            .first()
        )

        history_data = {
            "labels": [h.log_date.isoformat() for h in history],
            "deficit": [h.water_deficit_mm for h in history],
            "rainfall": [h.rainfall_mm for h in history],
        }

        recommendation = (
            compute_water_recommendation(plot.water_deficit_mm, plot.plot_size_acres)
            if plot.water_deficit_mm > 0
            else None
        )

        return render_template(
            "farmer/plot_detail.html",
            plot=plot,
            crop_profile=crop_profile,
            stage=stage,
            active_alert=active_alert,
            history_data=history_data,
            recommendation=recommendation,
        )


@farmer_bp.route("/plots/<int:plot_id>/resolve", methods=["POST"])
@login_required
@role_required("farmer")
def resolve_alert(plot_id: int):
    with get_session() as session:
        plot = _get_owned_plot_or_404(session, plot_id)
        alert = _active_alert_for_plot(session, plot.plot_id)
        if alert is None:
            flash("No active alert on this plot.", "error")
        else:
            apply_farmer_reply(session, alert, FarmerReply.CONFIRMED)
            flash("Marked as watered — alert resolved.", "success")
    return redirect(url_for("farmer.plot_detail", plot_id=plot_id))


@farmer_bp.route("/plots/<int:plot_id>/defer", methods=["POST"])
@login_required
@role_required("farmer")
def defer_alert(plot_id: int):
    with get_session() as session:
        plot = _get_owned_plot_or_404(session, plot_id)
        alert = _active_alert_for_plot(session, plot.plot_id)
        if alert is None:
            flash("No active alert on this plot.", "error")
        else:
            apply_farmer_reply(session, alert, FarmerReply.DEFERRED)
            flash("Got it — we'll remind you again.", "success")
    return redirect(url_for("farmer.plot_detail", plot_id=plot_id))


def _active_alert_for_plot(session, plot_id: int) -> AlertRecord | None:
    return (
        session.query(AlertRecord)
        .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
        .order_by(AlertRecord.created_at.desc())
        .first()
    )


@farmer_bp.route("/plots/<int:plot_id>/update-size", methods=["GET", "POST"])
@login_required
@role_required("farmer")
def update_plot_size(plot_id: int):
    with get_session() as session:
        plot = _get_owned_plot_or_404(session, plot_id)
        current_size = plot.plot_size_acres

    form = PlotSizeUpdateForm()
    if request.method == "GET":
        form.plot_size_acres.data = str(current_size)

    if form.validate_on_submit():
        with get_session() as session:
            plot = _get_owned_plot_or_404(session, plot_id)
            plot.plot_size_acres = float(form.plot_size_acres.data)
        flash("Plot size updated. Future recommendations will use the new size.", "success")
        return redirect(url_for("farmer.plot_detail", plot_id=plot_id))

    return render_template("farmer/plot_size_update.html", form=form, plot_id=plot_id)


@farmer_bp.route("/notifications")
@login_required
@role_required("farmer")
def notifications():
    with get_session() as session:
        messages = (
            session.query(SMSLog)
            .filter(SMSLog.phone_number == current_user.raw.phone_number)
            .order_by(SMSLog.created_at.desc())
            .limit(100)
            .all()
        )
    return render_template("farmer/notifications.html", messages=messages)
