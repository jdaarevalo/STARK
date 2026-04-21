"""
J.A.R.V.I.S. — Unified single-agent system for S.T.A.R.K.

Architecture: context injection, not tool calling.
All data is loaded from DuckDB in Python (~100ms) before the LLM call.
The model receives a single message with the full data context already embedded.
Result: 1 API round-trip per user message instead of 4-5.

Run via FastAPI:  uv run python src/chat/fastapi_app.py
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic_ai import Agent

from src.config.agents import get_google_model
from src.db.connection import StarkDatabase, load_athlete_config, load_daily_inputs

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.agents.jarvis_agent")

SYSTEM_PROMPT = """
You are J.A.R.V.I.S. — the athletic intelligence core of S.T.A.R.K.
You operate as a panel of three integrated experts who analyse the athlete's data
and deliver a single, unified recommendation. Never address them as separate voices;
synthesize into one coherent response.

THE THREE LENSES YOU ALWAYS APPLY:

  PHYSIOLOGIST — reads recovery signals (HRV, RHR, Body Battery, sleep).
    Decision trigger: if HRV delta < −10 ms vs 7d avg, RHR delta > +5 bpm, or
    Body Battery < 40 → override any hard session with active recovery or full rest.

  BIOMECHANICS COACH — reads running mechanics (cadence, GCT, VO, VR, fatigue drift).
    Decision trigger: cadence drift < −5 spm or GCT drift > +15 ms signals
    CNS fatigue — prescribe strength/drills before next quality session.

  SPORTS NUTRITIONIST — reads training load and session demands.
    Decision trigger: session > 60 min → prescribe carbohydrate strategy.
    ACR ratio > 1.5 → flag recovery nutrition priority.

OPERATING RULES:
1. Each message contains a DATA CONTEXT block with live telemetry. Use it as your source of truth.
2. Never invent numbers not present in the DATA CONTEXT.
3. If a data field is null or missing, say so — do not assume or fill in.
4. Tone: analytical, direct, slightly witty — J.A.R.V.I.S. from Iron Man. No filler.
5. Format responses as plain text with short paragraphs. No markdown headers.
"""


def build_agent() -> Agent:
    logger.info("J.A.R.V.I.S. unified agent initialized.")
    return Agent(
        model=get_google_model(),
        system_prompt=SYSTEM_PROMPT,
        output_type=str,
    )


agent = build_agent()


def build_context() -> str:
    """
    Loads all athlete data from DuckDB and JSON files synchronously (~100ms).
    Returns a structured string to be prepended to the user message.
    Called once per request in Python — no API round-trips.
    """
    db = StarkDatabase()
    config = load_athlete_config()
    today = date.today().isoformat()
    lthr = int(config.get("lthr", 0)) or None

    # Recovery
    readiness = db.get_readiness_snapshot(today)
    daily_inputs = load_daily_inputs()
    soreness = daily_inputs.get(today, {}).get("soreness", None)
    if soreness is not None:
        readiness["soreness_today"] = soreness
        if soreness >= 7:
            readiness["soreness_flag"] = "high — cancel high-intensity sessions"
        elif soreness >= 5:
            readiness["soreness_flag"] = "moderate — monitor load"

    # Load
    load = db.get_training_load_snapshot(lthr=lthr)

    # Biomechanics
    biomechanics = db.get_biomechanics_snapshot(lthr=lthr)

    # HR profile (FCmax observada + zonas)
    hr_profile = db.get_hr_profile(lthr=lthr)

    # Profile
    profile: dict = {
        "final_goal": "Sub 1:59 Half Marathon",
        "target_race": config.get("target_race_date"),
        "target_pace_min_per_km": config.get("target_pace_min_per_km"),
        "lthr_bpm": lthr,
    }
    if config.get("target_race_date"):
        try:
            race_date = date.fromisoformat(config["target_race_date"])
            profile["days_to_race"] = (race_date - date.today()).days
        except ValueError:
            pass
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

    context = {
        "date": today,
        "recovery": readiness,
        "training_load": load,
        "hr_profile": hr_profile,
        "last_3_runs_biomechanics": biomechanics,
        "athlete_profile": profile,
    }
    return f"[DATA CONTEXT]\n{json.dumps(context, indent=2, default=str)}\n[END DATA CONTEXT]\n\n"


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
            ctx = build_context()
            result = await agent.run(ctx + user_input, message_history=history)
            print(f"\n{result.output}\n")
            history = result.all_messages()

    asyncio.run(repl())
