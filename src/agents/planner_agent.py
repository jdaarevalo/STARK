import logging
import os
import sys
from pathlib import Path

# Ensure project root is in path when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from starlette.applications import Starlette

from src.db.connection import StarkDatabase
from src.models.workouts import DailyActionPlan

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.agents.planner_agent")

JARVIS_SYSTEM_PROMPT = """
You are S.T.A.R.K. (Smart Training & Athletic Readiness Kernel), an elite-level AI agent
specialized in sports physiology, biomechanical data science, and half marathon training.
Your primary objective is to optimize the athlete's performance and prevent injuries above all else.

STRICT OPERATING RULES:
1. FATIGUE ANALYSIS (Priority 0): If the athlete's HRV is below baseline, Body Battery is low
   (<40), or they report joint soreness (soreness_level > 5), you MUST cancel high-intensity
   sessions (VO2 Max, Intervals, Tempo) and prescribe active recovery or full rest.
2. PERIODIZATION: Ensure weekly plans follow the 80/20 rule (80% of volume in HR Zone 2,
   20% at high intensity).
3. NUTRITION & HYDRATION: Calculate hydration based on training load. If the suggested session
   exceeds 60 minutes, prescribe carbohydrate intake (gels/isotonic) during the session.
4. TONE: Your communication style must be professional, direct, analytical, and slightly witty —
   like J.A.R.V.I.S. from Iron Man. No robotic greetings.

Your output MUST be exclusively valid JSON strictly matching the provided schema.
"""


def build_agent() -> Agent:
    """Initializes the J.A.R.V.I.S. planner agent. Call once from main.py after load_dotenv()."""
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    model = GoogleModel("gemini-3.1-pro-preview", provider=GoogleProvider(api_key=api_key))
    logger.info("J.A.R.V.I.S. planner agent initialized.")
    return Agent(
        model=model,
        system_prompt=JARVIS_SYSTEM_PROMPT,
        output_type=DailyActionPlan,
    )


agent = build_agent()


@agent.tool
def get_athlete_readiness(ctx: RunContext[str], target_date: str) -> dict:
    """
    CRITICAL: Always call this before generating a training plan.
    Returns recovery metrics (sleep, Body Battery, HRV) for a specific date.
    target_date must be in 'YYYY-MM-DD' format.
    """
    db = StarkDatabase()
    data = db.get_daily_readiness(target_date)
    if not data:
        return {
            "status": "warning",
            "message": f"No biometric data found for {target_date}. Assume moderate recovery.",
        }
    return data


@agent.tool
def get_recent_runs(ctx: RunContext[str], limit: int = 4) -> list:
    """
    Returns detailed telemetry for the most recent runs.
    Includes distance, pace, cardiac load, biomechanics and effort metrics.
    Use this to assess accumulated training load, running economy and fatigue trends.
    """
    db = StarkDatabase()
    rows = db.get_recent_runs(limit)
    if not rows:
        return [{"status": "info", "message": "No recent runs found."}]

    runs = []
    for row in rows:
        activity_id = str(row["source_file"]).split("silver_run_")[-1].replace(".parquet", "")
        speed = row.get("avg_speed_m_s") or 0
        if speed > 0:
            pace_s = 1000 / speed
            pace = f"{int(pace_s // 60)}:{int(pace_s % 60):02d} min/km"
        else:
            pace = "N/A"
        runs.append({
            "activity_id": activity_id,
            "run_date": str(row["run_date"])[:10],
            "duration_minutes": round(row["duration_minutes"] or 0, 1),
            "total_distance_km": round((row["total_distance_meters"] or 0) / 1000, 2),
            "avg_pace": pace,
            "avg_power_w": round(row["avg_power_w"] or 0, 1),
            "max_power_w": round(row["max_power_w"] or 0, 1),
            "avg_heart_rate": round(row["avg_heart_rate"] or 0, 1),
            "max_heart_rate": round(row["max_heart_rate"] or 0, 1),
            "hr_zones_pct": {
                "z1_easy": row["pct_z1"],
                "z2_aerobic": row["pct_z2"],
                "z3_tempo": row["pct_z3"],
                "z4_threshold": row["pct_z4"],
                "z5_max": row["pct_z5"],
            },
            "avg_cadence_spm": round(row["avg_cadence_spm"] or 0, 1),
            "avg_step_length_mm": round(row["avg_step_length_mm"] or 0, 1),
            "avg_vertical_oscillation_mm": round(row["avg_vertical_oscillation_mm"] or 0, 1),
            "avg_vertical_ratio_pct": round(row["avg_vertical_ratio_pct"] or 0, 2),
            "avg_stance_time_ms": round(row["avg_stance_time_ms"] or 0, 1),
            "avg_temperature_c": round(row["avg_temperature_c"] or 0, 1),
        })
    return runs


@agent.tool
def get_athlete_context(ctx: RunContext[str]) -> dict:
    """
    Returns the athlete's profile (age, weight, goal, current shoes).
    Use weight for hydration recommendations and shoes to flag wear risk.
    """
    return {
        "age": 42,
        "weight_kg": 87.7,
        "current_shoes": "New Balance (47 km on them)",
        "final_goal": "Sub 1:59 Half Marathon",
        "next_goal": "2:10 Half Marathon",
        "target_race_date": "2026-05-17",
        "race_name": "Regensburg Marathon (HalbMarathon)",
    }


# Starlette app with built-in chat UI — run directly with uvicorn
app: Starlette = agent.to_web()

if __name__ == "__main__":
    from src.config.logging_config import setup_logging
    setup_logging()

    logger.info("Starting J.A.R.V.I.S. web interface at http://127.0.0.1:7932")
    uvicorn.run(app=app, host="127.0.0.1", port=7932)
