import os

from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

# Specialist agents (structured JSON output from real data) — quality matters.
_SPECIALIST_MODEL = "gemini-3.1-pro-preview"

# Orchestrator (routing + prose synthesis only) — speed matters.
_ORCHESTRATOR_MODEL = "gemini-3-flash-preview"


def _make_model(name: str) -> GoogleModel:
    load_dotenv()
    return GoogleModel(name, provider=GoogleProvider(api_key=os.getenv("GOOGLE_API_KEY")))


def get_google_model() -> GoogleModel:
    """Specialist model — used by planner and biomechanics agents."""
    return _make_model(_SPECIALIST_MODEL)


def get_orchestrator_model() -> GoogleModel:
    """Fast model — used by the J.A.R.V.I.S. orchestrator for routing and synthesis."""
    return _make_model(_ORCHESTRATOR_MODEL)
