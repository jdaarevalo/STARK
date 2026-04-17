"""
J.A.R.V.I.S. — Master orchestration layer for S.T.A.R.K.

Understands the athlete's intent and delegates to the right specialist agent(s).
Single entry point: replaces direct access to individual agent ports.

Run via Streamlit:  streamlit run main.py
Run via CLI:        uv run python src/agents/orchestrator.py
"""
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic_ai import Agent, RunContext

from src.agents.biomechanics_agent import agent as biomechanics_agent
from src.agents.planner_agent import agent as planner_agent
from src.config.agents import get_orchestrator_model

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.agents.orchestrator")

SYSTEM_PROMPT = """
You are J.A.R.V.I.S., the master coordination layer of S.T.A.R.K.
Your job: understand the athlete's question, delegate to the right specialist(s),
and synthesize a single coherent response.

SPECIALISTS AVAILABLE:

  consult_biomechanics_agent — HOW the athlete ran.
    Topics: cadence, vertical oscillation, ground contact time, stride mechanics,
    running efficiency, form analysis, fatigue patterns across recent runs.
    Trigger when the user asks about: technique, form, mechanics, how they ran,
    cadence, oscillation, stride, efficiency, "my runs".

  consult_planner_agent — WHAT the athlete should do (training decisions).
    Topics: daily training plan, rest vs. train decision, workout prescription,
    readiness from sleep/HRV/body battery, nutrition for a session.
    Trigger when the user asks about: today's plan, tomorrow's workout, should I
    rest, training plan, am I ready, what should I do.

ROUTING RULES:
1. Call ONLY the relevant specialist(s) — not all of them for every query.
2. If a query spans both domains, call both and synthesize into one response.
3. If the query is ambiguous, ask one clarifying question before delegating.
4. Never invent or extrapolate data not returned by a specialist.
5. Tone: analytical, direct, slightly witty. J.A.R.V.I.S. from Iron Man.
   No robotic greetings, no filler.
"""


def build_agent() -> Agent:
    logger.info("J.A.R.V.I.S. orchestrator initialized.")
    return Agent(model=get_orchestrator_model(), system_prompt=SYSTEM_PROMPT, output_type=str)


agent = build_agent()


@agent.tool
async def consult_biomechanics_agent(ctx: RunContext[str], focus: str = "") -> str:
    """
    Delegate to the Biomechanics Specialist to analyze running mechanics from the last 3 runs.

    Args:
        focus: Optional specific aspect to analyze (e.g. "cadence", "ground contact time",
               "fatigue patterns"). Leave empty for a comprehensive analysis.
    """
    prompt = f"Analyze the athlete's running biomechanics for the last 3 runs. Today: {date.today().isoformat()}."
    if focus:
        prompt += f" Pay special attention to: {focus}."
    result = await biomechanics_agent.run(prompt)
    return result.output.model_dump_json(indent=2)


@agent.tool
async def consult_planner_agent(ctx: RunContext[str], target_date: str = "") -> str:
    """
    Delegate to the Training Planner to generate a daily plan and readiness assessment.

    Args:
        target_date: Date in YYYY-MM-DD format. Defaults to today if not specified.
    """
    if not target_date:
        target_date = date.today().isoformat()
    result = await planner_agent.run(
        f"Generate a training plan and readiness assessment for {target_date}."
    )
    return result.output.model_dump_json(indent=2)


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
            result = await agent.run(user_input, message_history=history)
            print(f"\n{result.output}\n")
            history = result.all_messages()

    asyncio.run(repl())
