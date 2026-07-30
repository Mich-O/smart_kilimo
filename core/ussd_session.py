"""
core/ussd_session.py
USSD state machine for the Africa's Talking USSD channel.

Protocol notes:
- Every response must start with "CON " (show next menu, session continues)
  or "END " (final message, session closes).
- Africa's Talking sends the *cumulative* input string on every request:
  first request text="", after first digit text="1", after second
  text="1*2", etc. We only ever care about the *last* segment — the
  digit(s) the farmer just typed for the current menu.
- Responses must stay under 182 characters (AT's per-page limit).

Session state (current_step, active_plot_id, pending_selection) is
persisted to the USSDSession row between requests, since each USSD request
is a fresh, stateless HTTP call from AT's side.
"""
from __future__ import annotations

from datetime import date

from core.constants import PLOT_SIZE_PRESETS
from core.enums import AlertStatus, CropType, FarmerReply, RegionCode, USSDStep
from core.models import AlertRecord, CropProfile, Farmer, FarmPlot, RegionProfile, USSDSession
from db.database import get_session
from services.plot_evaluation import apply_farmer_reply

MAX_USSD_LENGTH = 182

REGION_MENU_ORDER = [RegionCode.EASTERN, RegionCode.RIFT_VALLEY, RegionCode.COAST]
CROP_MENU_ORDER = [CropType.MAIZE, CropType.BEANS, CropType.ONIONS]


def handle_ussd(session_id: str, phone_number: str, text: str, service_code: str) -> str:
    """
    Entry point called by the /ussd route. Returns the full response string,
    already prefixed with "CON " or "END ".
    """
    last_input = _last_segment(text)

    with get_session() as db:
        session_row = db.get(USSDSession, session_id)
        if session_row is None:
            session_row = USSDSession(
                session_id=session_id,
                phone_number=phone_number,
                current_step=USSDStep.WELCOME.value,
            )
            db.add(session_row)
            db.flush()

        farmer = db.get(Farmer, phone_number)

        # Very first request of the session (text == "") always resets to
        # the correct entry point, regardless of any stale session row.
        if text == "":
            response = _route_entry(db, session_row, farmer)
        else:
            response = _route_step(db, session_row, farmer, last_input)

        db.flush()

    return _clamp(response)


def _last_segment(text: str) -> str:
    if not text:
        return ""
    return text.split("*")[-1]


def _clamp(response: str) -> str:
    prefix, _, body = response.partition(" ")
    if len(response) <= MAX_USSD_LENGTH:
        return response
    allowed_body_len = MAX_USSD_LENGTH - len(prefix) - 1
    return f"{prefix} {body[:allowed_body_len]}"


# ---------------------------------------------------------------------------
# Entry routing (text == "", brand-new session)
# ---------------------------------------------------------------------------

def _route_entry(db, session_row: USSDSession, farmer) -> str:
    if farmer is None:
        session_row.current_step = USSDStep.WELCOME.value
        return "CON Welcome to Smart Kilimo\n1. Register\n2. Exit"

    plots = list(farmer.plots)
    if len(plots) == 0:
        session_row.current_step = USSDStep.WELCOME.value
        return "CON Welcome to Smart Kilimo\n1. Register\n2. Exit"

    if len(plots) == 1:
        session_row.current_step = USSDStep.STATUS_CHECK.value
        session_row.active_plot_id = plots[0].plot_id
        return _status_check_response(plots[0])

    session_row.current_step = USSDStep.SELECT_PLOT.value
    return _plot_list_response(plots)


# ---------------------------------------------------------------------------
# Step routing (subsequent requests within a session)
# ---------------------------------------------------------------------------

def _route_step(db, session_row: USSDSession, farmer, last_input: str) -> str:
    step = USSDStep(session_row.current_step)

    handlers = {
        USSDStep.WELCOME: _handle_welcome,
        USSDStep.CONFIRM_REGISTER: _handle_confirm_register,
        USSDStep.SELECT_REGION: _handle_select_region,
        USSDStep.SELECT_CROP: _handle_select_crop,
        USSDStep.SELECT_PLOT_SIZE: _handle_select_plot_size,
        USSDStep.SELECT_PLOT: _handle_select_plot,
        USSDStep.STATUS_CHECK: _handle_status_check,
        USSDStep.UPDATE_PLOT_SIZE: _handle_update_plot_size,
    }

    handler = handlers.get(step)
    if handler is None:
        return "END Sorry, something went wrong. Please dial in again."

    return handler(db, session_row, farmer, last_input)


def _handle_welcome(db, session_row: USSDSession, farmer, last_input: str) -> str:
    if last_input == "1":
        session_row.current_step = USSDStep.CONFIRM_REGISTER.value
        return _region_menu_response()
    if last_input == "2":
        return "END Goodbye. Dial in anytime to register with Smart Kilimo."
    return "END Invalid option. Please dial in again."


def _handle_confirm_register(db, session_row: USSDSession, farmer, last_input: str) -> str:
    region = _region_from_choice(last_input)
    if region is None:
        return "END Invalid region selection. Please dial in again."

    session_row.pending_selection = region.value
    session_row.current_step = USSDStep.SELECT_CROP.value
    return _crop_menu_response()


def _handle_select_region(db, session_row: USSDSession, farmer, last_input: str) -> str:
    # Used when an already-registered farmer is adding a new plot.
    region = _region_from_choice(last_input)
    if region is None:
        return "END Invalid region selection. Please dial in again."

    session_row.pending_selection = region.value
    session_row.current_step = USSDStep.SELECT_CROP.value
    return _crop_menu_response()


def _handle_select_crop(db, session_row: USSDSession, farmer, last_input: str) -> str:
    crop = _crop_from_choice(last_input)
    if crop is None:
        return "END Invalid crop selection. Please dial in again."

    region_code = session_row.pending_selection
    if region_code is None:
        return "END Session expired. Please dial in again."

    session_row.pending_selection = f"{region_code}:{crop.value}"
    session_row.current_step = USSDStep.SELECT_PLOT_SIZE.value
    return _plot_size_menu_response()


def _handle_select_plot_size(db, session_row: USSDSession, farmer, last_input: str) -> str:
    plot_size = _plot_size_from_choice(last_input)
    if plot_size is None:
        return "END Invalid option. Please dial in again."

    pending = session_row.pending_selection or ""
    region_code, _, crop_type = pending.partition(":")
    if not region_code or not crop_type:
        return "END Session expired. Please dial in again."

    plot = _create_farmer_and_plot(
        db, session_row.phone_number, RegionCode(region_code), CropType(crop_type), plot_size
    )
    session_row.current_step = USSDStep.REGISTRATION_COMPLETE.value
    session_row.active_plot_id = plot.plot_id
    session_row.pending_selection = None

    return (
        f"END Registration complete!\n"
        f"Plot: {plot.plot_label} ({plot_size} acre)\n"
        f"You will receive an SMS when your crop needs water."
    )


def _handle_select_plot(db, session_row: USSDSession, farmer, last_input: str) -> str:
    plots = list(farmer.plots) if farmer else []

    if last_input == str(len(plots) + 1):
        # "Add new plot"
        session_row.current_step = USSDStep.SELECT_REGION.value
        return _region_menu_response()

    try:
        choice_index = int(last_input) - 1
    except ValueError:
        return "END Invalid option. Please dial in again."

    if choice_index < 0 or choice_index >= len(plots):
        return "END Invalid option. Please dial in again."

    selected_plot = plots[choice_index]
    session_row.current_step = USSDStep.STATUS_CHECK.value
    session_row.active_plot_id = selected_plot.plot_id
    return _status_check_response(selected_plot)


def _handle_status_check(db, session_row: USSDSession, farmer, last_input: str) -> str:
    plot = db.get(FarmPlot, session_row.active_plot_id)
    if plot is None:
        return "END Session expired. Please dial in again."

    if plot.alert_active:
        if last_input == "1":
            alert = _active_alert_for_plot(db, plot.plot_id)
            if alert is not None:
                apply_farmer_reply(db, alert, FarmerReply.CONFIRMED)
            return "END Thank you. Marked as watered."
        if last_input == "2":
            alert = _active_alert_for_plot(db, plot.plot_id)
            if alert is not None:
                apply_farmer_reply(db, alert, FarmerReply.DEFERRED)
            return "END Got it, we'll remind you again."
        if last_input == "3":
            session_row.current_step = USSDStep.UPDATE_PLOT_SIZE.value
            return _plot_size_menu_response()
        return "END Invalid option. Please dial in again."

    # No active alert: only option is to correct the plot size.
    if last_input == "1":
        session_row.current_step = USSDStep.UPDATE_PLOT_SIZE.value
        return _plot_size_menu_response()
    if last_input == "2":
        return "END Goodbye."
    return "END Invalid option. Please dial in again."


def _handle_update_plot_size(db, session_row: USSDSession, farmer, last_input: str) -> str:
    plot_size = _plot_size_from_choice(last_input)
    if plot_size is None:
        return "END Invalid option. Please dial in again."

    plot = db.get(FarmPlot, session_row.active_plot_id)
    if plot is None:
        return "END Session expired. Please dial in again."

    plot.plot_size_acres = plot_size
    return (
        f"END Plot size updated to {plot_size} acre.\n"
        f"Future water recommendations will use this size."
    )


def _active_alert_for_plot(db, plot_id: int) -> AlertRecord | None:
    return (
        db.query(AlertRecord)
        .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
        .order_by(AlertRecord.created_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _region_menu_response() -> str:
    lines = ["CON Select region:"]
    for i, region in enumerate(REGION_MENU_ORDER, start=1):
        lines.append(f"{i}. {_region_display_name(region)}")
    return "\n".join(lines)


def _crop_menu_response() -> str:
    lines = ["CON Select crop:"]
    for i, crop in enumerate(CROP_MENU_ORDER, start=1):
        lines.append(f"{i}. {crop.value.title()}")
    return "\n".join(lines)


def _plot_size_menu_response() -> str:
    lines = ["CON Select plot size:"]
    for i, (_, label) in enumerate(PLOT_SIZE_PRESETS, start=1):
        lines.append(f"{i}. {label}")
    return "\n".join(lines)


def _plot_list_response(plots) -> str:
    lines = ["CON Your plots:"]
    for i, plot in enumerate(plots, start=1):
        lines.append(f"{i}. {plot.plot_label}")
    lines.append(f"{len(plots) + 1}. Add new plot")
    return "\n".join(lines)


def _status_check_response(plot: FarmPlot) -> str:
    status = "NEEDS WATER" if plot.alert_active else "OK"
    stage_name = _current_stage_name(plot)
    header = (
        f"Plot: {plot.plot_label} ({plot.plot_size_acres} acre)\n"
        f"Stage: {stage_name} (day {plot.crop_age_days})\n"
        f"Deficit: {plot.water_deficit_mm}mm\n"
        f"Status: {status}"
    )
    if plot.alert_active:
        return f"CON {header}\n1. Mark as watered\n2. Remind me later\n3. Update plot size"
    return f"CON {header}\n1. Update plot size\n2. Exit"


def _current_stage_name(plot: FarmPlot) -> str:
    from core.irrigation_engine import get_stage_for_age  # local import avoids a cycle

    crop_profile = plot.crop
    if crop_profile is None:
        return "Unknown"
    stage = get_stage_for_age(crop_profile, plot.crop_age_days)
    return stage.stage_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _region_from_choice(choice: str) -> RegionCode | None:
    try:
        index = int(choice) - 1
    except ValueError:
        return None
    if index < 0 or index >= len(REGION_MENU_ORDER):
        return None
    return REGION_MENU_ORDER[index]


def _crop_from_choice(choice: str) -> CropType | None:
    try:
        index = int(choice) - 1
    except ValueError:
        return None
    if index < 0 or index >= len(CROP_MENU_ORDER):
        return None
    return CROP_MENU_ORDER[index]


def _plot_size_from_choice(choice: str) -> float | None:
    try:
        index = int(choice) - 1
    except ValueError:
        return None
    if index < 0 or index >= len(PLOT_SIZE_PRESETS):
        return None
    return PLOT_SIZE_PRESETS[index][0]


def _region_display_name(region: RegionCode) -> str:
    names = {
        RegionCode.EASTERN: "Eastern",
        RegionCode.RIFT_VALLEY: "Rift Valley",
        RegionCode.COAST: "Coast",
    }
    return names[region]


def _create_farmer_and_plot(
    db, phone_number: str, region_code: RegionCode, crop_type: CropType, plot_size_acres: float
) -> FarmPlot:
    farmer = db.get(Farmer, phone_number)
    if farmer is None:
        farmer = Farmer(phone_number=phone_number)
        db.add(farmer)
        db.flush()

    region = db.get(RegionProfile, region_code.value)
    crop = db.get(CropProfile, crop_type.value)

    # Note: the (phone_number, crop_type, region_code) unique constraint on
    # FarmPlot means a farmer can never register the same crop+region twice,
    # so plot_label needs no numeric disambiguator in practice.
    plot_label = f"{crop.name} {region.name}"

    plot = FarmPlot(
        phone_number=phone_number,
        plot_label=plot_label,
        region_code=region_code.value,
        crop_type=crop_type.value,
        plot_size_acres=plot_size_acres,
        planting_date=date.today(),
        crop_age_days=0,
        water_deficit_mm=0.0,
        dry_maturing_override=False,
        alert_active=False,
    )
    db.add(plot)
    db.flush()
    return plot
