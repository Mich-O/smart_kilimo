"""
scripts/seed_demo_data.py
Creates a handful of demo farmers with plots and runs each through ten
simulated days of weather, using the exact same production code path the
real engine uses (services.plot_evaluation.evaluate_and_apply) -- this is
not a mock or a shortcut, it's the identical function the real scheduled
cycle and the admin operations tools call. Exists purely so a freshly
running dev instance has real, varied data on its dashboards instead of
one empty page.

Usage (run from the project root, with your .env already configured):
    python scripts/seed_demo_data.py

Safe to re-run: it skips any demo phone number that already has a web
account, so it won't create duplicates or pile up extra history. To start
over, delete the rows yourself (there's no public "reset" button in the
real app -- that was intentionally a sandbox-only behaviour, not
something production should expose).

All demo farmers share one password so you can log in as any of them
from /farmer/login and see the farmer-side view too, not just admin.
"""
from __future__ import annotations

import os
import sys
from datetime import date

# Makes `python scripts/seed_demo_data.py` work regardless of the current
# working directory, by putting the project root (one level up from this
# file) on the import path before importing anything from the app.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEMO_PASSWORD = "DemoPass123!"

DEMO_FARMERS = [
    {
        "phone_number": "+254700000101",
        "full_name": "Grace Wanjiru",
        "plots": [
            {"region_code": "EASTERN", "crop_type": "MAIZE", "plot_size_acres": 0.25, "start_age_days": 20},
            {"region_code": "EASTERN", "crop_type": "BEANS", "plot_size_acres": 0.25, "start_age_days": 20},
        ],
    },
    {
        "phone_number": "+254700000102",
        "full_name": "Peter Otieno",
        "plots": [
            {"region_code": "RIFT_VALLEY", "crop_type": "BEANS", "plot_size_acres": 0.5, "start_age_days": 18},
        ],
    },
    {
        "phone_number": "+254700000103",
        "full_name": "Mary Achieng",
        "plots": [
            {"region_code": "COAST", "crop_type": "ONIONS", "plot_size_acres": 0.125, "start_age_days": 35},
        ],
    },
    {
        "phone_number": "+254700000104",
        "full_name": "John Kamau",
        "plots": [
            {"region_code": "RIFT_VALLEY", "crop_type": "MAIZE", "plot_size_acres": 1.0, "start_age_days": 22},
        ],
    },
]

# A realistic ten-day rainfall run: mostly dry, one light near-miss (below
# the reset threshold), one real reset -- enough to show a threshold
# crossing, an alert, and a recovery on the dashboard chart without
# needing twenty-plus days of simulation.
DAILY_RAINFALL_MM = [0, 0, 0, 4, 0, 0, 9, 0, 0, 0]


def main() -> None:
    from api.app import create_app
    from config import settings

    app = create_app()

    if not settings.seed_demo_data:
        print("SEED_DEMO_DATA is not enabled -- skipping (this is the safe default; set SEED_DEMO_DATA=true to opt in).")
        return

    with app.app_context():
        from core.enums import ApiStatus
        from core.models import CropProfile, Farmer, FarmPlot, RegionProfile
        from db.database import get_session
        from services.auth import hash_password
        from services.plot_evaluation import evaluate_and_apply
        from services.weather import WeatherReading

        sms_client = app.config["SMS_CLIENT"]
        created_farmers = 0
        created_plots = 0
        skipped_farmers = []

        for farmer_data in DEMO_FARMERS:
            phone = farmer_data["phone_number"]
            plot_ids = []

            with get_session() as session:
                existing = session.get(Farmer, phone)
                if existing is not None and existing.password_hash is not None:
                    skipped_farmers.append(phone)
                    continue

                if existing is None:
                    farmer = Farmer(
                        phone_number=phone,
                        full_name=farmer_data["full_name"],
                        password_hash=hash_password(DEMO_PASSWORD),
                    )
                    session.add(farmer)
                    session.flush()
                else:
                    existing.full_name = farmer_data["full_name"]
                    existing.password_hash = hash_password(DEMO_PASSWORD)
                created_farmers += 1

                for plot_data in farmer_data["plots"]:
                    region = session.get(RegionProfile, plot_data["region_code"])
                    crop = session.get(CropProfile, plot_data["crop_type"])
                    plot = FarmPlot(
                        phone_number=phone,
                        plot_label=f"{crop.name} {region.name}",
                        region_code=plot_data["region_code"],
                        crop_type=plot_data["crop_type"],
                        plot_size_acres=plot_data["plot_size_acres"],
                        planting_date=date.today(),
                        crop_age_days=plot_data["start_age_days"],
                        water_deficit_mm=0.0,
                        dry_maturing_override=False,
                        alert_active=False,
                    )
                    session.add(plot)
                    session.flush()
                    plot_ids.append(plot.plot_id)
                    created_plots += 1

            # Run each plot through the rainfall pattern -- one real
            # get_session()-scoped evaluation per simulated day, exactly
            # like the production cycle does, just compressed into one
            # script run instead of spread across ten real calendar days.
            for plot_id in plot_ids:
                for rainfall_mm in DAILY_RAINFALL_MM:
                    with get_session() as session:
                        plot = session.get(FarmPlot, plot_id)
                        reading = WeatherReading(
                            region_code=plot.region_code,
                            poll_date=date.today(),
                            rainfall_mm=rainfall_mm,
                            api_status=ApiStatus.SUCCESS,
                        )
                        evaluate_and_apply(session, plot, reading, sms_client)

        print(
            f"Created/updated {created_farmers} farmer(s), {created_plots} plot(s), "
            f"each run through {len(DAILY_RAINFALL_MM)} simulated days."
        )
        if skipped_farmers:
            print(f"Skipped (already has a web account): {', '.join(skipped_farmers)}")
        print(f"\nLog in as any demo farmer at /farmer/login with password: {DEMO_PASSWORD}")
        for f in DEMO_FARMERS:
            print(f"  {f['phone_number']}  ({f['full_name']})")


if __name__ == "__main__":
    # Deliberately defensive: this script is chained before gunicorn in the
    # startCommand (see render.yaml) so a hosted demo instance gets sample
    # data on every boot without needing shell access. If it's chained via
    # `&&`, any unhandled exception here would stop gunicorn from starting
    # at all -- a demo-data hiccup must never take the real app down with
    # it, so failures are logged and swallowed rather than propagated.
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see comment above
        print(f"seed_demo_data.py failed, continuing without demo data: {exc}")
