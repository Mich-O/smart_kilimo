"""
db/seed.py
Seed data for regions, crops, and crop growth stages. Idempotent: safe to
call on every startup, only inserts rows that don't already exist.

ETo reference used to precompute daily_deficit_constant_mm = ETo * Kc:
    ETo = 5.0 mm/day (East African average)

---

deficit_alert_threshold_mm derivation (FAO-56 depletion-fraction model):

    threshold(stage) = p * TAW_PER_METER_MM * root_depth_m(stage)

  p (depletion_fraction) -- FAO-56 Table 22's "no-stress" allowable
    depletion fraction, valid at the reference ETc = 5mm/day this system
    already assumes for ETo (see ETO_MM_PER_DAY above). Constant per
    crop, not per stage.

  TAW_PER_METER_MM -- total available water per metre of root depth.
    Imported from core.constants; represents a generic medium loam
    (field capacity 0.30, wilting point 0.13). This is the single
    biggest simplifying assumption in the whole model: real soil varies
    by region and this system doesn't collect soil type. Revisit this
    constant first if real soil-survey data ever becomes available for
    the target regions.

  root_depth_m(stage) -- effective root depth at that growth stage,
    approximated from FAO-56 Table 22's typical rooting-depth curve
    (shallow at emergence, reaching maximum by mid-season, staying there
    through late season) mapped onto each crop's own stage windows below.
    Not measured for local varieties -- an approximation of a known
    general pattern, not a locally-calibrated figure.

The upshot: thresholds now correctly shrink for shallow-rooted young
plants (an onion seedling can't tolerate much depletion) and grow for
deep-rooted mature ones (a tasseling maize plant genuinely can draw on a
much larger soil-water reserve before it needs topping up) -- rather than
one flat, stage-blind number per crop.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from config import settings
from core.constants import TAW_PER_METER_MM
from core.enums import CropType, RegionCode
from core.models import Administrator, CropProfile, CropStage, RegionProfile

ETO_MM_PER_DAY = 5.0

REGIONS = [
    {"region_code": RegionCode.EASTERN.value, "name": "Eastern Kenya", "latitude": -0.05, "longitude": 38.00, "elevation_m": 1200.0},
    {"region_code": RegionCode.RIFT_VALLEY.value, "name": "Rift Valley", "latitude": -0.30, "longitude": 35.85, "elevation_m": 1800.0},
    {"region_code": RegionCode.COAST.value, "name": "Coast Region", "latitude": -3.30, "longitude": 39.85, "elevation_m": 50.0},
]

# depletion_fraction (p): FAO-56 Table 22, at ETc = 5mm/day.
CROPS = [
    {
        "crop_type": CropType.MAIZE.value,
        "name": "Maize",
        "total_season_days": 120,
        "depletion_fraction": 0.55,
        "stages": [
            {"stage_name": "Germination", "start_day": 1, "end_day": 20, "kc_value": 0.40, "root_depth_m": 0.20, "is_maturing_stage": False},
            {"stage_name": "Vegetative", "start_day": 21, "end_day": 55, "kc_value": 0.80, "root_depth_m": 0.70, "is_maturing_stage": False},
            {"stage_name": "Tasseling", "start_day": 56, "end_day": 75, "kc_value": 1.20, "root_depth_m": 1.30, "is_maturing_stage": False},
            {"stage_name": "Grain Fill", "start_day": 76, "end_day": 100, "kc_value": 1.00, "root_depth_m": 1.30, "is_maturing_stage": False},
            {"stage_name": "Maturing", "start_day": 101, "end_day": 120, "kc_value": 0.35, "root_depth_m": 1.30, "is_maturing_stage": True},
        ],
    },
    {
        "crop_type": CropType.BEANS.value,
        "name": "Beans",
        "total_season_days": 90,
        "depletion_fraction": 0.45,
        "stages": [
            {"stage_name": "Germination", "start_day": 1, "end_day": 15, "kc_value": 0.40, "root_depth_m": 0.15, "is_maturing_stage": False},
            {"stage_name": "Vegetative", "start_day": 16, "end_day": 40, "kc_value": 0.70, "root_depth_m": 0.35, "is_maturing_stage": False},
            {"stage_name": "Flowering", "start_day": 41, "end_day": 65, "kc_value": 1.10, "root_depth_m": 0.60, "is_maturing_stage": False},
            {"stage_name": "Pod Fill", "start_day": 66, "end_day": 80, "kc_value": 0.90, "root_depth_m": 0.60, "is_maturing_stage": False},
            {"stage_name": "Maturing", "start_day": 81, "end_day": 90, "kc_value": 0.30, "root_depth_m": 0.60, "is_maturing_stage": True},
        ],
    },
    {
        "crop_type": CropType.ONIONS.value,
        "name": "Onions",
        "total_season_days": 150,
        "depletion_fraction": 0.30,
        "stages": [
            {"stage_name": "Establishment", "start_day": 1, "end_day": 30, "kc_value": 0.50, "root_depth_m": 0.10, "is_maturing_stage": False},
            {"stage_name": "Vegetative", "start_day": 31, "end_day": 70, "kc_value": 0.70, "root_depth_m": 0.20, "is_maturing_stage": False},
            {"stage_name": "Bulbing", "start_day": 71, "end_day": 120, "kc_value": 1.00, "root_depth_m": 0.35, "is_maturing_stage": False},
            {"stage_name": "Ripening", "start_day": 121, "end_day": 140, "kc_value": 0.75, "root_depth_m": 0.35, "is_maturing_stage": False},
            {"stage_name": "Maturing", "start_day": 141, "end_day": 150, "kc_value": 0.50, "root_depth_m": 0.35, "is_maturing_stage": True},
        ],
    },
]


def run_seed(session: Session) -> None:
    """Idempotent seed of regions, crops, and stages. Call at app startup."""
    _seed_regions(session)
    _seed_crops(session)
    session.flush()


def ensure_default_admin(session: Session) -> None:
    """
    Creates one Administrator account from DEFAULT_ADMIN_USERNAME /
    DEFAULT_ADMIN_PASSWORD if no admin account exists yet. This exists so
    a freshly deployed instance has a working admin login without needing
    shell access to run a management command — change the password after
    first login, or set your own via env vars before first deploy.
    """
    from services.auth import hash_password  # local import: auth imports models, avoid a cycle

    if session.query(Administrator).count() > 0:
        return
    session.add(
        Administrator(
            username=settings.default_admin_username,
            password_hash=hash_password(settings.default_admin_password),
        )
    )
    session.flush()


def _seed_regions(session: Session) -> None:
    existing = {r.region_code for r in session.query(RegionProfile.region_code).all()}
    for region in REGIONS:
        if region["region_code"] in existing:
            continue
        session.add(RegionProfile(**region))


def _seed_crops(session: Session) -> None:
    existing = {c.crop_type for c in session.query(CropProfile.crop_type).all()}
    for crop in CROPS:
        if crop["crop_type"] in existing:
            continue
        profile = CropProfile(
            crop_type=crop["crop_type"],
            name=crop["name"],
            total_season_days=crop["total_season_days"],
        )
        session.add(profile)
        depletion_fraction = crop["depletion_fraction"]
        for stage in crop["stages"]:
            daily_deficit_constant_mm = round(ETO_MM_PER_DAY * stage["kc_value"], 4)
            deficit_alert_threshold_mm = round(
                depletion_fraction * TAW_PER_METER_MM * stage["root_depth_m"], 1
            )
            session.add(
                CropStage(
                    crop_type=crop["crop_type"],
                    stage_name=stage["stage_name"],
                    start_day=stage["start_day"],
                    end_day=stage["end_day"],
                    kc_value=stage["kc_value"],
                    daily_deficit_constant_mm=daily_deficit_constant_mm,
                    deficit_alert_threshold_mm=deficit_alert_threshold_mm,
                    is_maturing_stage=stage["is_maturing_stage"],
                )
            )
