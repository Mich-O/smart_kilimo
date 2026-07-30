"""
tests/test_daily_cycle.py
Integration-style tests for services.daily_cycle.run_daily_cycle, using an
isolated SQLite DB (see conftest.py), a FakeWeatherClient (patched in for
services.weather.fetch_rainfall), and a FakeSMSClient in place of
AfricasTalkingClient.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.enums import AlertStatus
from core.models import AlertRecord, Farmer, FarmPlot
from db.database import get_session, init_db
from db.seed import run_seed
from services import daily_cycle
from tests.fakes.fake_sms import FakeSMSClient
from tests.fakes.fake_weather import FakeWeatherClient


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    with get_session() as session:
        run_seed(session)
    yield
    # Clean out per-test rows (keep seeded reference data).
    from core.models import AlertRecord, FarmPlot, Farmer, IrrigationLog, PlotDailyLog, SMSLog, USSDSession, WeatherRecord
    with get_session() as session:
        for model in (IrrigationLog, AlertRecord, SMSLog, USSDSession, PlotDailyLog, FarmPlot, Farmer, WeatherRecord):
            session.query(model).delete()


def make_plot(phone="+254700000001", region="EASTERN", crop="MAIZE", **overrides):
    with get_session() as session:
        if session.get(Farmer, phone) is None:
            session.add(Farmer(phone_number=phone))
            session.flush()
        defaults = dict(
            phone_number=phone,
            plot_label=f"{crop.title()} {region.title()}",
            region_code=region,
            crop_type=crop,
            plot_size_acres=0.25,
            planting_date=date.today(),
            crop_age_days=10,
            water_deficit_mm=0.0,
            dry_maturing_override=False,
            alert_active=False,
        )
        defaults.update(overrides)
        plot = FarmPlot(**defaults)
        session.add(plot)
        session.flush()
        return plot.plot_id


def test_alert_fires_and_sends_sms():
    # Vegetative Maize threshold is now 65.5mm (FAO-56 depletion-fraction
    # derivation, see db/seed.py) rather than the old flat 40mm.
    plot_id = make_plot(water_deficit_mm=62.0, crop_age_days=25)  # Vegetative, delta 4mm, threshold 65.5mm

    fake_weather = FakeWeatherClient()
    fake_weather.set_rainfall("EASTERN", 0.0)
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        summary = daily_cycle.run_daily_cycle(fake_sms)

    assert summary.total_alerts == 1
    assert summary.total_errors == 0
    assert len(fake_sms.sent) == 1

    with get_session() as session:
        plot = session.get(FarmPlot, plot_id)
        assert plot.alert_active is True
        assert plot.water_deficit_mm == 66.0


def test_significant_rain_resets_deficit():
    plot_id = make_plot(water_deficit_mm=25.0, crop_age_days=25)

    fake_weather = FakeWeatherClient()
    fake_weather.set_rainfall("EASTERN", 10.0)  # above default 5mm reset threshold
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        summary = daily_cycle.run_daily_cycle(fake_sms)

    assert summary.total_resets == 1
    assert summary.total_alerts == 0
    with get_session() as session:
        plot = session.get(FarmPlot, plot_id)
        assert plot.water_deficit_mm == 0.0


def test_maturing_stage_suppresses_alerts():
    plot_id = make_plot(water_deficit_mm=0.0, crop_age_days=110)  # Maize maturing stage (101-120)

    fake_weather = FakeWeatherClient()
    fake_weather.set_rainfall("EASTERN", 0.0)
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        summary = daily_cycle.run_daily_cycle(fake_sms)

    assert summary.total_suppressed == 1
    assert summary.total_alerts == 0
    assert len(fake_sms.sent) == 0
    with get_session() as session:
        plot = session.get(FarmPlot, plot_id)
        assert plot.dry_maturing_override is True


def test_failed_weather_poll_does_not_block_other_plots():
    make_plot(phone="+254700000001", region="EASTERN", crop="MAIZE", water_deficit_mm=62.0, crop_age_days=25)
    make_plot(phone="+254700000002", region="COAST", crop="MAIZE", water_deficit_mm=62.0, crop_age_days=25)

    fake_weather = FakeWeatherClient()
    fake_weather.set_failure("EASTERN", __import__("core.enums", fromlist=["ApiStatus"]).ApiStatus.TIMEOUT)
    fake_weather.set_rainfall("COAST", 0.0)
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        summary = daily_cycle.run_daily_cycle(fake_sms)

    assert summary.total_plots == 2
    assert summary.total_errors == 0
    # EASTERN plot: weather failed -> INCREMENT no-op, no alert.
    # COAST plot: weather succeeded -> crosses threshold -> ALERT.
    assert summary.total_alerts == 1
    assert len(fake_sms.sent) == 1
    assert fake_sms.sent[0].phone_number == "+254700000002"


def test_reminder_pass_sends_after_interval_and_expires_after_max():
    from core.models import AlertRecord

    plot_id = make_plot(water_deficit_mm=63.0, crop_age_days=25, alert_active=True)
    with get_session() as session:
        old_time = datetime.now(timezone.utc) - timedelta(hours=10)
        session.add(
            AlertRecord(
                plot_id=plot_id,
                phone_number="+254700000001",
                deficit_at_trigger_mm=63.0,
                reminder_count=0,
                status=AlertStatus.ACTIVE.value,
                created_at=old_time,
            )
        )

    fake_weather = FakeWeatherClient()
    fake_weather.set_rainfall("EASTERN", 0.0)
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        summary = daily_cycle.run_daily_cycle(fake_sms)

    assert summary.total_reminders == 1
    # One send this cycle: the evaluation pass sees deficit still above
    # threshold but does NOT re-alert or re-send, since there's already
    # an ACTIVE AlertRecord for this plot (see plot_evaluation.py) --
    # only the reminder pass fires, on its own interval.
    assert len(fake_sms.sent) == 1


def test_repeated_evaluation_does_not_duplicate_an_active_alert():
    """
    Regression test: running the evaluation pass on the same plot many
    consecutive days while it stays above threshold must create exactly
    ONE AlertRecord and send exactly ONE alert SMS, not one per day.
    Discovered when building the farmer web "mark as watered" action --
    resolving the most recent of several duplicate ACTIVE alerts left
    older ones stuck open underneath it.
    """
    plot_id = make_plot(water_deficit_mm=62.0, crop_age_days=25)  # Vegetative, threshold 65.5mm

    fake_weather = FakeWeatherClient()
    fake_weather.set_rainfall("EASTERN", 0.0)
    fake_sms = FakeSMSClient()

    with patch.object(daily_cycle, "fetch_rainfall", fake_weather.fetch_rainfall):
        for _ in range(5):
            daily_cycle.run_daily_cycle(fake_sms)

    with get_session() as session:
        active_alerts = (
            session.query(AlertRecord)
            .filter(AlertRecord.plot_id == plot_id, AlertRecord.status == AlertStatus.ACTIVE.value)
            .all()
        )
        assert len(active_alerts) == 1

    alert_sms_count = sum(1 for m in fake_sms.sent if "Smart Kilimo Alert" in m.message)
    assert alert_sms_count == 1
