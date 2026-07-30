"""
core/models.py
All SQLAlchemy ORM models for Smart Kilimo (SQLAlchemy 2.0 declarative style).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from core.enums import (
    AlertStatus,
    ApiStatus,
    CropType,
    FarmerReply,
    RegionCode,
    SMSDirection,
    USSDStep,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Farmer(Base):
    """Farmer identity + web login, keyed by phone number."""

    __tablename__ = "farmers"

    phone_number: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Nullable because USSD can create a bare Farmer row before any web
    # account exists for that number (see core/ussd_session.py). A farmer
    # can't log in to the website until password_hash is set.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    plots: Mapped[List["FarmPlot"]] = relationship(
        back_populates="farmer", cascade="all, delete-orphan"
    )


class RegionProfile(Base):
    """GPS coordinates used for weather API calls, per region."""

    __tablename__ = "region_profiles"

    region_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    weather_records: Mapped[List["WeatherRecord"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )
    plots: Mapped[List["FarmPlot"]] = relationship(back_populates="region")


class CropProfile(Base):
    """Agronomic constants per crop type."""

    __tablename__ = "crop_profiles"

    crop_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    total_season_days: Mapped[int] = mapped_column(Integer, nullable=False)

    stages: Mapped[List["CropStage"]] = relationship(
        back_populates="crop", cascade="all, delete-orphan", order_by="CropStage.start_day"
    )
    plots: Mapped[List["FarmPlot"]] = relationship(back_populates="crop")


class CropStage(Base):
    """One growth stage within a crop's season. Composition of CropProfile."""

    __tablename__ = "crop_stages"

    stage_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crop_type: Mapped[str] = mapped_column(ForeignKey("crop_profiles.crop_type"), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_day: Mapped[int] = mapped_column(Integer, nullable=False)
    end_day: Mapped[int] = mapped_column(Integer, nullable=False)
    kc_value: Mapped[float] = mapped_column(Float, nullable=False)
    daily_deficit_constant_mm: Mapped[float] = mapped_column(Float, nullable=False)
    # FAO-56 depletion-fraction trigger for THIS stage's root depth -- see
    # db/seed.py for the derivation (p x TAW_per_metre x root_depth_m).
    # Correctly varies per stage: a shallow-rooted seedling can't tolerate
    # the same depletion a mature, deep-rooted plant can.
    deficit_alert_threshold_mm: Mapped[float] = mapped_column(Float, nullable=False)
    is_maturing_stage: Mapped[bool] = mapped_column(Boolean, default=False)

    crop: Mapped["CropProfile"] = relationship(back_populates="stages")


class FarmPlot(Base):
    """One registered crop/region combination for a farmer. Multi-plot model."""

    __tablename__ = "farm_plots"
    __table_args__ = (
        UniqueConstraint("phone_number", "crop_type", "region_code", name="uq_farmer_crop_region"),
    )

    plot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(ForeignKey("farmers.phone_number"), nullable=False)
    plot_label: Mapped[str] = mapped_column(String(80), nullable=False)
    region_code: Mapped[str] = mapped_column(ForeignKey("region_profiles.region_code"), nullable=False)
    crop_type: Mapped[str] = mapped_column(ForeignKey("crop_profiles.crop_type"), nullable=False)
    plot_size_acres: Mapped[float] = mapped_column(Float, nullable=False)
    planting_date: Mapped[date] = mapped_column(Date, nullable=False)
    crop_age_days: Mapped[int] = mapped_column(Integer, default=0)
    water_deficit_mm: Mapped[float] = mapped_column(Float, default=0.0)
    dry_maturing_override: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    farmer: Mapped["Farmer"] = relationship(back_populates="plots")
    region: Mapped["RegionProfile"] = relationship(back_populates="plots")
    crop: Mapped["CropProfile"] = relationship(back_populates="plots")


class WeatherRecord(Base):
    """One weather poll per region per day (idempotency enforced by unique constraint)."""

    __tablename__ = "weather_records"
    __table_args__ = (
        UniqueConstraint("region_code", "poll_date", name="uq_region_poll_date"),
    )

    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(ForeignKey("region_profiles.region_code"), nullable=False)
    poll_date: Mapped[date] = mapped_column(Date, nullable=False)
    rainfall_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    api_status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApiStatus.SUCCESS.value)
    error_detail: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    polled_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    region: Mapped["RegionProfile"] = relationship(back_populates="weather_records")


class USSDSession(Base):
    """Live session state during a USSD interaction."""

    __tablename__ = "ussd_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Intentionally NOT a ForeignKey: a USSD session starts the moment an
    # unregistered phone dials in, before any Farmer row exists for it.
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    current_step: Mapped[str] = mapped_column(String(30), default=USSDStep.WELCOME.value)
    active_plot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("farm_plots.plot_id"), nullable=True
    )
    last_input: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Free-form scratch field for values collected mid-flow (e.g. chosen region
    # before crop is picked). Stored as "REGION_CODE" or "REGION_CODE:CROP_TYPE".
    pending_selection: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class AlertRecord(Base):
    """One alert event per plot."""

    __tablename__ = "alert_records"

    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plot_id: Mapped[int] = mapped_column(ForeignKey("farm_plots.plot_id"), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)  # denormalised
    deficit_at_trigger_mm: Mapped[float] = mapped_column(Float, nullable=False)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=AlertStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IrrigationLog(Base):
    """Audit of every farmer reply to an alert."""

    __tablename__ = "irrigation_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plot_id: Mapped[int] = mapped_column(ForeignKey("farm_plots.plot_id"), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)  # denormalised
    alert_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alert_records.alert_id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # FarmerReply
    deficit_at_action_mm: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SMSLog(Base):
    """Every inbound and outbound SMS."""

    __tablename__ = "sms_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Intentionally NOT a ForeignKey, same reasoning as USSDSession.phone_number:
    # an inbound SMS can arrive from a number with no Farmer row at all (wrong
    # number, unregistered sender) and must still be logged, not rejected.
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # SMSDirection
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    message_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PlotDailyLog(Base):
    """
    One row per plot per evaluation (real cycle or admin-simulated day).
    Exists purely so farmer/admin dashboards can chart real deficit and
    rainfall history instead of only showing the current snapshot.
    """

    __tablename__ = "plot_daily_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plot_id: Mapped[int] = mapped_column(ForeignKey("farm_plots.plot_id"), nullable=False)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    rainfall_mm: Mapped[float] = mapped_column(Float, nullable=False)
    water_deficit_mm: Mapped[float] = mapped_column(Float, nullable=False)
    crop_age_days: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # IrrigationAction
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SystemConfig(Base):
    """Key-value runtime config table."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Administrator(Base):
    """Admin users, for the operations dashboard."""

    __tablename__ = "administrators"

    admin_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
