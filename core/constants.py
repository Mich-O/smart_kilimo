"""
core/constants.py
Shared domain constants for converting the engine's water-balance deficit
(millimetres of depth -- the FAO-56 standard unit) into an actual volume a
farmer can act on, plus the plot-size presets offered on both the USSD and
web registration flows.

Why this exists: 1mm of water depth applied over 1 square metre of soil is
exactly 1 litre (1mm = 0.001m; 0.001m x 1m^2 = 0.001m^3 = 1L). So the water
a plot needs is a function of its deficit AND its area -- there's no such
thing as a correct flat "litres per plant" figure independent of plot
size, which is what the original seed data guessed at.

TAW_PER_METER_MM lives here (rather than in db/seed.py, where it's used)
because it's a physical soil constant, not a crop-specific input -- see
db/seed.py's module docstring for how it feeds into each stage's
deficit_alert_threshold_mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

ACRE_TO_SQUARE_METERS = 4046.8564224
JERRYCAN_LITERS = 20.0
BOWSER_LITERS = 10_000.0  # standard water-bowser delivery size, common in rural Kenya

# Total available water per metre of root depth, for a representative
# medium loam (field capacity 0.30, wilting point 0.13). This is the
# single biggest simplifying assumption in the whole irrigation model:
# real soil varies by region and this system doesn't collect soil type.
# See db/seed.py for how it's combined with each crop's FAO-56 depletion
# fraction and each stage's root depth to derive alert thresholds.
TAW_PER_METER_MM = 170.0

# FAO-56's guidance for furrow/hand-applied irrigation (as opposed to
# precision drip) puts application efficiency at roughly 60-75%: some of
# what's applied is lost to runoff and deep seepage before the root zone
# gets it. 0.7 is the middle of that range. This means the volume a
# farmer is told to apply is somewhat more than the crop's strict
# requirement, which is intentional and correct, not a rounding fudge.
IRRIGATION_APPLICATION_EFFICIENCY = 0.7

# Rough estimate of what a smallholder with a modest pump and furrow
# access could reasonably apply in a single day of sustained irrigation
# (not hand-carried -- see the bowser-load reference above, this system
# already assumes pump/canal delivery at real plot scale). Not a measured
# figure, a deliberately conservative planning assumption. When a
# recommendation exceeds this, the advisory says so honestly instead of
# implying one number a farmer has no realistic way to act on today.
# Tune this if you have real data on your farmers' irrigation capacity.
MAX_PRACTICAL_DAILY_LITERS = 150_000.0

# Presented as menu choices on USSD (digit-driven) and as quick-fill
# presets on the web form (which also accepts an exact custom value).
PLOT_SIZE_PRESETS = [
    (0.125, "1/8 acre"),
    (0.25, "1/4 acre"),
    (0.5, "1/2 acre"),
    (1.0, "1 acre"),
    (2.0, "2+ acres"),
]


@dataclass(frozen=True)
class WaterRecommendation:
    liters: float
    jerrycans: int
    cubic_meters: float
    bowser_loads: float
    sessions_needed: int
    liters_per_session: float
    plot_size_acres: float

    @property
    def sms_phrase(self) -> str:
        # Leads with m3/L -- the physically meaningful units at real plot
        # scale -- rather than jerrycans: a quarter-acre plot needing a
        # ~40mm irrigation genuinely requires tens of cubic metres, which
        # is furrow/canal/pump/bowser-delivered water, not something
        # carried by hand. Jerrycans are still useful as a scale
        # reference, so they're included, just not framed as "carry N of
        # these" the way a flat per-plant figure wrongly implied.
        #
        # The plot size is included INSIDE this phrase (not appended
        # separately by the caller) specifically so the phased branch
        # below can end on its own complete sentence -- appending
        # "across your X acre plot" after "...keep going daily until it's
        # done" used to read as a dangling, ungrammatical afterthought.
        if self.sessions_needed <= 1:
            return f"about {self.cubic_meters}m3 ({round(self.liters)}L) across your {self.plot_size_acres} acre plot"
        return (
            f"about {self.cubic_meters}m3 across your {self.plot_size_acres} acre plot -- start today "
            f"with what you can; with typical smallholder means this may take ~{self.sessions_needed} "
            f"days total, so keep watering daily until done"
        )


def compute_water_recommendation(deficit_mm: float, plot_size_acres: float) -> WaterRecommendation:
    """
    Converts a water deficit (mm) over a plot (acres) into an actual
    volume to apply, after accounting for irrigation application losses,
    and phases it into multiple sessions when the total exceeds what's
    realistically deliverable in one day (MAX_PRACTICAL_DAILY_LITERS).
    Pure function -- no I/O, safe to call from core.irrigation_engine.
    """
    area_m2 = plot_size_acres * ACRE_TO_SQUARE_METERS
    net_liters = deficit_mm * area_m2
    gross_liters = net_liters / IRRIGATION_APPLICATION_EFFICIENCY
    jerrycans = max(1, math.ceil(gross_liters / JERRYCAN_LITERS))
    sessions_needed = max(1, math.ceil(gross_liters / MAX_PRACTICAL_DAILY_LITERS))
    return WaterRecommendation(
        liters=round(gross_liters, 1),
        jerrycans=jerrycans,
        cubic_meters=round(gross_liters / 1000, 2),
        bowser_loads=round(gross_liters / BOWSER_LITERS, 2),
        sessions_needed=sessions_needed,
        liters_per_session=round(gross_liters / sessions_needed, 1),
        plot_size_acres=plot_size_acres,
    )
