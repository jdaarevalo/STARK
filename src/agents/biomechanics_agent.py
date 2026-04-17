"""
Biomechanics Analysis Agent — J.A.R.V.I.S. sub-system.

Analyzes the last N runs from the Gold layer, identifies biomechanical patterns
(cadence, vertical oscillation, ground contact time, stride length, vertical ratio,
fatigue drift, cardiac load) and returns structured recommendations.

Run: uv run python src/agents/biomechanics_agent.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from pydantic_ai import Agent, RunContext
from starlette.applications import Starlette

from src.config.agents import get_google_model
from src.db.connection import StarkDatabase
from src.models.biometrics import BiomechanicsReport, format_pace

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.agents.biomechanics_agent")


# ── Agent ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are the Biomechanics Analysis sub-system of S.T.A.R.K., an elite sports science AI
specialized in running economy, injury prevention, and neuromuscular efficiency.

Your ONLY job in this conversation is to analyze the athlete's running mechanics data
from the last 3 runs and return a structured biomechanics report.

REFERENCE VALUES you must use to benchmark the athlete:
  Cadence:
    - <160 SPM → severe overstriding / shuffle risk
    - 160-165 SPM → below optimal for a half-marathon runner
    - 166-170 SPM → acceptable, room for improvement
    - 170-180 SPM → optimal aerobic running cadence
    - >180 SPM → acceptable for tempo/intervals

  Vertical Oscillation (VO):
    - <60 mm → very efficient (elite range)
    - 60-70 mm → excellent
    - 70-80 mm → good
    - 80-90 mm → slightly excessive bounce, energy wasted
    - >90 mm → significant efficiency loss

  Vertical Ratio (VR = VO / step_length × 100):
    - <7%   → excellent
    - 7-8%  → good
    - 8-9%  → moderate, target improvement
    - 9-10% → inefficient upward energy
    - >10%  → significant mechanical waste

  Ground Contact Time (GCT / Stance Time):
    - <230 ms → elite / fast tempo range
    - 230-260 ms → good
    - 260-280 ms → acceptable at easy/aerobic pace
    - >280 ms → heavy landing, energy leak, injury risk
    - High std (>20 ms) → inconsistent mechanics, fatigue or form breakdown

  Cardiac Drift (HR last third − HR first third):
    - <5 bpm → excellent aerobic base
    - 5-10 bpm → normal for easy runs
    - 10-15 bpm → moderate — check hydration and aerobic fitness
    - >15 bpm → significant — reduce pace or reassess training load

  Cadence Drift (cadence last third − cadence first third):
    - Negative values → fatigue-induced shuffle, CNS fatigue
    - Stable or slight increase → good neuromuscular endurance

ANALYSIS PROTOCOL:
1. Call get_run_biomechanics to retrieve the last 3 runs.
2. For each run, compute cardiac drift and cadence drift from the first/last third fields.
3. Identify trends across the 3 runs (improving, degrading, or inconsistent).
4. Apply the reference values above to classify each metric.
5. Prioritize focus metrics by impact on: (a) injury risk, (b) running economy, (c) race time.
6. Your tone: analytical, direct, slightly witty — like J.A.R.V.I.S. No filler sentences.

Output MUST be valid JSON matching the schema exactly.
"""


def build_agent() -> Agent:
    """Initializes the Biomechanics Analysis agent."""
    logger.info("Biomechanics agent initialized.")
    return Agent(
        model=get_google_model(),
        system_prompt=SYSTEM_PROMPT,
        output_type=BiomechanicsReport,
    )


agent = build_agent()


@agent.tool
def get_run_biomechanics(ctx: RunContext[str], limit: int = 3) -> list:
    """
    Returns detailed biomechanics telemetry for the most recent runs.
    Includes per-run averages, variability (std), and fatigue drift
    (first third vs last third of each run) for: cadence, vertical oscillation,
    vertical ratio, ground contact time, step length, power, heart rate, and pace.
    """
    db = StarkDatabase()
    rows = db.get_run_biomechanics(limit)
    if not rows:
        return [{"status": "error", "message": "No run data found in the Gold layer."}]

    result = []
    for row in rows:
        pace = format_pace(row.get("avg_speed_m_s") or 0)
        pace_f = format_pace(row.get("speed_first_third_m_s") or 0)
        pace_l = format_pace(row.get("speed_last_third_m_s") or 0)

        result.append({
            "run_date": str(row["run_date"])[:10],
            "total_distance_km": round((row["total_distance_m"] or 0) / 1000, 2),
            "duration_min": round(row["duration_min"] or 0, 1),
            "avg_pace": pace,
            # Cadence
            "avg_cadence_spm": row["avg_cadence_spm"],
            "std_cadence_spm": row["std_cadence_spm"],
            "cadence_first_third_spm": row["cadence_first_third_spm"],
            "cadence_last_third_spm": row["cadence_last_third_spm"],
            # Vertical Oscillation
            "avg_vo_mm": row["avg_vo_mm"],
            "std_vo_mm": row["std_vo_mm"],
            # Vertical Ratio
            "avg_vr_pct": row["avg_vr_pct"],
            # Ground Contact Time
            "avg_gct_ms": row["avg_gct_ms"],
            "std_gct_ms": row["std_gct_ms"],
            "gct_first_third_ms": row["gct_first_third_ms"],
            "gct_last_third_ms": row["gct_last_third_ms"],
            # Step Length
            "avg_step_length_mm": row["avg_step_length_mm"],
            "std_step_length_mm": row["std_step_length_mm"],
            # Power
            "avg_power_w": row["avg_power_w"],
            "max_power_w": row["max_power_w"],
            "std_power_w": row["std_power_w"],
            # Heart Rate & Zones
            "avg_hr": row["avg_hr"],
            "max_hr": row["max_hr"],
            "hr_zones_pct": {
                "z1_easy": row["pct_z1"],
                "z2_aerobic": row["pct_z2"],
                "z3_tempo": row["pct_z3"],
                "z4_threshold": row["pct_z4"],
                "z5_max": row["pct_z5"],
            },
            # Fatigue drift
            "pace_first_third": pace_f,
            "pace_last_third": pace_l,
            "hr_first_third": row["hr_first_third"],
            "hr_last_third": row["hr_last_third"],
            # Elevation
            "elevation_range_m": row["elevation_range_m"],
        })
    return result


# ── Web app ───────────────────────────────────────────────────────────────────

app: Starlette = agent.to_web()

if __name__ == "__main__":
    from src.config.logging_config import setup_logging
    setup_logging()

    logger.info("Starting Biomechanics Agent at http://127.0.0.1:7933")
    uvicorn.run(app=app, host="127.0.0.1", port=7933)
