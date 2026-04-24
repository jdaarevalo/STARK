"""
J.A.R.V.I.S. — Unified single-agent system for S.T.A.R.K.

Architecture: lightweight context injection + on-demand tools.
A minimal recovery + profile snapshot is always injected (~400 tokens).
Deeper data (load, biomechanics, run history, ad-hoc SQL) is fetched via tools
only when the user's question requires it — keeping the base call fast and cheap.

Run via FastAPI:  uv run python src/chat/fastapi_app.py
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Generator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic_ai import Agent

from src.config.agents import get_google_model
from src.db.connection import StarkDatabase, load_athlete_config, load_daily_inputs, load_race_predictions, load_training_plan

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.agents.jarvis_agent")

# Schema hint passed to query_athlete_data so the LLM knows what columns exist.
_GOLD_SCHEMA = """
Available DuckDB views (SELECT only):

gold_health — one row per day
  date, resting_heart_rate, hrv_last_night_avg, hrv_status,
  body_battery_end, avg_stress_level, vo2_max

gold_sleep — one row per day
  date, sleep_score, sleep_score_qualifier, sleep_time_seconds,
  deep_sleep_seconds, rem_sleep_seconds, avg_heart_rate, avg_stress

gold_runs — one row per GPS second per activity
  timestamp, source_file, distance (m), heart_rate, cadence (half-cadence → x2 for SPM),
  enhanced_speed (m/s), power (W), stance_time (ms), vertical_oscillation (mm),
  vertical_ratio (%), step_length (mm), enhanced_altitude (m), temperature (°C)

gold_hydration — one row per day (may not exist)
  date, intake_ml, goal_ml, sweat_loss_ml

gold_weight — one row per day (may not exist)
  date, weight_kg, bmi, body_fat_pct, muscle_mass_kg

Notes:
- cadence in gold_runs is HALF-cadence; multiply by 2 for steps-per-minute.
- To aggregate per run: GROUP BY source_file, use MIN(timestamp) as run date.
- All dates are castable with CAST(x AS DATE).
- HOUR(timestamp) extracts the hour for time-of-day analysis.
"""

SYSTEM_PROMPT = f"""
You are J.A.R.V.I.S. — the athletic intelligence core of S.T.A.R.K.
You operate as a panel of three integrated experts who analyse the athlete's data
and deliver a single, unified recommendation. Never address them as separate voices;
synthesize into one coherent response.

THE THREE LENSES YOU ALWAYS APPLY:

  PHYSIOLOGIST — reads recovery signals (HRV, RHR, Body Battery, sleep).
    Decision trigger: if HRV delta < −10 ms vs 7d avg, RHR delta > +5 bpm, or
    Body Battery < 40 → override any hard session with active recovery or full rest.

  BIOMECHANICS COACH — reads running mechanics (cadence, GCT, VO, VR, fatigue drift).
    GCT reference: <230 ms elite · 230-260 good · 260-280 acceptable easy pace · >280 heavy landing.
    GCT drift (last_third − first_third): <+5 ms stable · +5–15 ms moderate fatigue · >+15 ms CNS fatigue.
    Cadence drift: negative (dropping) = fatigue shuffle · stable or rising = good neuromuscular endurance.
    Decision trigger: GCT drift > +15 ms OR cadence drift < −5 spm → prescribe strength/drills before next quality session.

  SPORTS NUTRITIONIST — reads training load and session demands.
    Decision trigger: session > 60 min → prescribe carbohydrate strategy.
    ACR ratio > 1.5 → flag recovery nutrition priority.

OPERATING RULES:
1. Each message contains a DATA CONTEXT block with today's recovery snapshot and athlete profile.
2. For deeper analysis use tools — call only the ones you actually need.
3. Never invent numbers not present in DATA CONTEXT or tool results.
4. If a data field is null or missing, say so — do not assume or fill in.
5. Tone: analytical, direct, slightly witty — J.A.R.V.I.S. from Iron Man. No filler.
6. Format responses as plain text with short paragraphs. No markdown headers.

TOOL USAGE GUIDE:
- get_training_load         → load/fatigue/injury-risk questions, session planning
- get_recent_runs(n)        → questions about specific past runs, last session, weekly volume
- get_health_trend(days)    → multi-day HRV/sleep/RHR trend questions
- get_upcoming_workouts()   → full week Garmin Coach schedule, upcoming session details
- query_athlete_data(sql)   → anything not covered above (time-of-day patterns, correlations, etc.)

NOTE: The DATA CONTEXT already contains today's and tomorrow's scheduled workouts under
'garmin_coach_today_tomorrow'. Call get_upcoming_workouts() only when the user asks about
the full week or sessions beyond tomorrow.

GOLD LAYER SCHEMA (for query_athlete_data):
{_GOLD_SCHEMA}
"""


def build_agent() -> Agent:
    logger.info("J.A.R.V.I.S. unified agent initialized.")
    return Agent(
        model=get_google_model(),
        system_prompt=SYSTEM_PROMPT,
        output_type=str,
    )


agent = build_agent()


# ── Tools ──────────────────────────────────────────────────────────────────────

@agent.tool_plain
def get_training_load() -> dict:
    """
    Returns the current ATL/CTL/TSB training load snapshot.
    Use for: training load questions, session planning, injury risk,
    "should I run today?", carbohydrate/recovery nutrition advice.
    """
    db = StarkDatabase()
    config = load_athlete_config()
    lthr = int(config.get("lthr", 0)) or None
    return db.get_training_load_snapshot(lthr=lthr)


@agent.tool_plain
def get_recent_runs(limit: int = 5) -> list:
    """
    Returns detailed telemetry + biomechanics for the last N runs (default 5), newest first.
    Each run includes: date, distance_km, duration_min, avg_pace, avg_hr, hr_zones_pct,
    avg_cadence_spm, avg_gct_ms, avg_power_w, and fatigue drift metrics
    (cadence_drift_spm, hr_drift_bpm, pace_first_third, pace_last_third).
    Use for: questions about specific past runs, last session analysis,
    weekly volume, biomechanics review, "how was my last run?", post-run nutrition.
    Pass limit=1 for last run only, up to 10 for trend analysis.
    """
    db = StarkDatabase()
    config = load_athlete_config()
    lthr = int(config.get("lthr", 0)) or None
    return db.get_biomechanics_snapshot(lthr=lthr)


@agent.tool_plain
def get_health_trend(days: int = 14) -> list:
    """
    Returns daily health metrics for the last N days (default 14), oldest first.
    Each row: date, resting_heart_rate, hrv_last_night_avg, hrv_status,
    body_battery_end, avg_stress_level, vo2_max, sleep_score, sleep_hours.
    Use for: multi-day HRV/sleep/RHR trend questions, recovery pattern analysis.
    """
    db = StarkDatabase()
    return db.get_health_trend(days=days)


@agent.tool_plain
def get_upcoming_workouts() -> dict:
    """
    Returns the full week of Garmin Coach scheduled workouts.
    Use for: questions about the training plan, weekly schedule, what sessions
    are coming up, how to prepare for an upcoming workout, weekly load preview.
    Each workout includes: date, name, description (with target paces),
    duration_min, type (AEROBIC_BASE / THRESHOLD / ANAEROBIC_CAPACITY), sport, status.
    """
    return load_training_plan()


@agent.tool_plain
def query_athlete_data(sql: str) -> list:
    """
    Executes a read-only SQL SELECT against the athlete's DuckDB gold layer.
    Use for any question not covered by the other tools:
    - Time-of-day performance (morning vs evening runs)
    - Custom aggregations, correlations, historical queries
    - Any analysis requiring raw data not pre-computed elsewhere
    Only SELECT statements are permitted. Returns up to 100 rows as a list of dicts.
    """
    sql_stripped = sql.strip().lstrip(";").strip()
    if not sql_stripped.upper().startswith("SELECT"):
        return [{"error": "Only SELECT statements are permitted."}]
    db = StarkDatabase()
    try:
        result = db.conn.execute(sql_stripped).df()
        return result.head(100).to_dict(orient="records")
    except Exception as e:
        logger.error("query_athlete_data failed: %s | sql: %s", e, sql_stripped)
        return [{"error": str(e)}]


# ── Context injection (lightweight snapshot only) ──────────────────────────────

def build_context_with_steps() -> Generator[tuple[str, str | None], None, None]:
    """
    Loads the lightweight recovery + profile snapshot, yielding (step_label, detail)
    tuples as each dataset is loaded so the caller can stream progress to the UI.
    Final yield is ("__context__", <json_string>) with the full context payload.
    """
    db = StarkDatabase()
    config = load_athlete_config()
    today = date.today().isoformat()
    lthr = int(config.get("lthr", 0)) or None

    # Recovery snapshot
    yield ("Scanning recovery telemetry…", None)
    readiness = db.get_readiness_snapshot(today)
    daily_inputs = load_daily_inputs()
    soreness = daily_inputs.get(today, {}).get("soreness", None)
    if soreness is not None:
        readiness["soreness_today"] = soreness
        if soreness >= 7:
            readiness["soreness_flag"] = "high — cancel high-intensity sessions"
        elif soreness >= 5:
            readiness["soreness_flag"] = "moderate — monitor load"

    hrv = readiness.get("hrv_ms")
    hrv_delta = readiness.get("hrv_delta_ms")
    bb = readiness.get("body_battery")
    rhr = readiness.get("rhr_bpm")
    detail_parts = []
    if hrv is not None:
        sign = "+" if (hrv_delta or 0) >= 0 else ""
        detail_parts.append(f"HRV {hrv} ms ({sign}{hrv_delta} vs 7d avg)")
    if bb is not None:
        detail_parts.append(f"Body Battery {bb}")
    if rhr is not None:
        detail_parts.append(f"RHR {rhr} bpm")
    if soreness is not None:
        detail_parts.append(f"Soreness {soreness}/10")
    yield ("Recovery data loaded", " · ".join(detail_parts) if detail_parts else None)

    # Athlete profile
    yield ("Loading athlete profile…", None)
    profile: dict = {
        "final_goal": "Sub 1:59 Half Marathon",
        "target_race": config.get("target_race_date"),
        "target_pace_min_per_km": config.get("target_pace_min_per_km"),
        "lthr_bpm": lthr,
    }
    if config.get("target_race_date"):
        try:
            race_date = date.fromisoformat(config["target_race_date"])
            days_left = (race_date - date.today()).days
            profile["days_to_race"] = days_left
            yield ("Athlete profile loaded", f"{days_left} days to race · Target pace {config.get('target_pace_min_per_km')} min/km")
        except ValueError:
            yield ("Athlete profile loaded", None)
    else:
        yield ("Athlete profile loaded", None)

    shoes = config.get("shoes", [])
    if shoes:
        profile["shoes"] = []
        for shoe in shoes:
            km_used = db.get_km_since(shoe.get("start_date", "2000-01-01"))
            max_km = shoe.get("max_km", 600)
            pct = round(km_used / max_km * 100, 1) if max_km else 0
            profile["shoes"].append({
                "name": shoe["name"],
                "km_used": km_used,
                "max_km": max_km,
                "pct_life_used": pct,
                "status": "replace soon" if pct >= 90 else ("monitor" if pct >= 75 else "ok"),
            })

    # Weight snapshot (lightweight — latest only, no full history)
    yield ("Loading weight…", None)
    weight = db.get_weight_snapshot(days=30)
    if weight.get("latest_kg"):
        delta = weight.get("delta_kg_over_period")
        sign = "+" if (delta or 0) > 0 else ""
        w_detail = f"{weight['latest_kg']} kg as of {weight['latest_date']}"
        if delta is not None:
            w_detail += f" ({sign}{delta} kg over 30d)"
        yield ("Weight loaded", w_detail)
        weight_summary = {
            "latest_kg": weight["latest_kg"],
            "latest_date": weight["latest_date"],
            "delta_kg_over_30d": weight.get("delta_kg_over_period"),
        }
    else:
        yield ("Weight", "No data")
        weight_summary = {"status": "no_data"}

    # Garmin Coach — today & tomorrow only (lightweight)
    yield ("Loading Garmin Coach schedule…", None)
    training_plan = load_training_plan()
    today_tomorrow = training_plan.get("today_tomorrow", [])
    if today_tomorrow:
        names = " · ".join(f"{w['date']}: {w['name']} ({w['duration_min']} min)" for w in today_tomorrow)
        yield ("Schedule loaded", names)
    else:
        yield ("Schedule", "No workouts today/tomorrow — rest day or no plan data")

    # Race predictions
    yield ("Loading Garmin race predictions…", None)
    race_predictions = load_race_predictions()
    if race_predictions:
        hm = race_predictions.get("half_marathon", {})
        yield ("Race predictions loaded", f"5K {race_predictions['5k']['formatted']} · 10K {race_predictions['10k']['formatted']} · HM {hm.get('formatted')} · Marathon {race_predictions['marathon']['formatted']}")
    else:
        yield ("Race predictions", "No data — run Sync to extract")

    context = {
        "date": today,
        "recovery": readiness,
        "athlete_profile": profile,
        "weight": weight_summary,
        "garmin_coach_today_tomorrow": today_tomorrow,
        "garmin_race_predictions": race_predictions,
        "note": "Use tools to fetch training load, recent runs, health trends, full weekly schedule, or run custom SQL queries.",
    }
    yield ("__context__", json.dumps(context, indent=2, default=str))


if __name__ == "__main__":
    import asyncio
    from src.config.logging_config import setup_logging
    setup_logging()

    async def repl() -> None:
        print("J.A.R.V.I.S. online. Type 'exit' to quit.\n")
        history = []
        while True:
            try:
                user_input = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue
            if not history:
                ctx_json = ""
                for label, detail in build_context_with_steps():
                    if label == "__context__":
                        ctx_json = detail
                    else:
                        print(f"  ⚡ {label}" + (f" — {detail}" if detail else ""))
                prompt = f"[DATA CONTEXT]\n{ctx_json}\n[END DATA CONTEXT]\n\n{user_input}"
            else:
                prompt = user_input
            result = await agent.run(prompt, message_history=history)
            print(f"\n{result.output}\n")
            history = result.all_messages()

    asyncio.run(repl())