"""
Unit tests for the J.A.R.V.I.S. agent tools.

Each tool is exercised directly (bypassing the LLM) by importing the underlying
function from the tool registry and calling it with a patched StarkDatabase and
config. The agent module is NOT imported at the top level to avoid instantiating
the Google model client during test collection.
"""
import json
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    from src.db import connection
    connection.StarkDatabase._instance = None
    yield
    connection.StarkDatabase._instance = None


def _make_run_parquet(path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_parquet(path, index=False)


@pytest.fixture
def db_with_runs(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()

    pd.DataFrame([{
        "date": pd.Timestamp("2026-03-21"),
        "sleep_time_seconds": 28800, "deep_sleep_seconds": 5400,
        "rem_sleep_seconds": 7200, "sleep_score": 85,
        "sleep_score_qualifier": "GOOD", "avg_heart_rate": 55.0,
        "avg_stress": 18.0, "light_sleep_seconds": 14400,
        "awake_sleep_seconds": 1800, "avg_respiration": 15.0,
        "avg_spo2": 96.0, "sleep_score_feedback": "POSITIVE",
    }]).to_parquet(processed / "silver_sleep_data.parquet", index=False)

    pd.DataFrame([{
        "date": pd.Timestamp("2026-03-21"),
        "resting_heart_rate": 52, "avg_stress_level": 17,
        "steps": 8000, "active_calories": 400.0,
        "total_distance_meters": 6000.0, "hrv_weekly_avg": 37,
        "hrv_last_night_avg": 38, "hrv_status": "BALANCED",
        "body_battery_end": 86, "training_status_phrase": None, "vo2_max": 43.0,
    }]).to_parquet(processed / "silver_health_telemetry.parquet", index=False)

    run_rows = [
        {
            "timestamp": pd.Timestamp("2026-03-21 07:00:00") + pd.Timedelta(minutes=i),
            "distance": float(i * 167),
            "heart_rate": 135,
            "cadence": 80,
            "enhanced_speed": 2.8,
            "power": 220.0,
            "step_length": 1100.0,
            "vertical_oscillation": 85.0,
            "vertical_ratio": 8.5,
            "stance_time": 250.0,
            "temperature": 18.0,
            "enhanced_altitude": 100.0,
        }
        for i in range(60)
    ]
    _make_run_parquet(processed / "silver_run_001.parquet", run_rows)

    with patch("src.db.connection.PROCESSED_DIR", processed):
        from src.db.connection import StarkDatabase
        db = StarkDatabase()
        yield db
        db.close()


def _tool_fn(tool_name: str):
    """Retrieve the raw function from the agent's tool registry without invoking the LLM."""
    from src.agents.jarvis_agent import agent
    return agent._function_toolset.tools[tool_name].function


# ── get_training_load ─────────────────────────────────────────────────────────

def test_get_training_load_returns_snapshot_with_lthr(db_with_runs):
    fn = _tool_fn("get_training_load")
    with (
        patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs),
        patch("src.agents.jarvis_agent.load_athlete_config", return_value={"lthr": 165}),
    ):
        result = fn()
    assert isinstance(result, dict)
    assert "ctl_fitness" in result
    assert "atl_fatigue" in result
    assert "tsb_form" in result


def test_get_training_load_no_lthr_returns_no_data(db_with_runs):
    fn = _tool_fn("get_training_load")
    with (
        patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs),
        patch("src.agents.jarvis_agent.load_athlete_config", return_value={"lthr": 0}),
    ):
        result = fn()
    assert result.get("status") == "no_data"


# ── get_recent_runs ───────────────────────────────────────────────────────────

def test_get_recent_runs_returns_list(db_with_runs):
    fn = _tool_fn("get_recent_runs")
    with (
        patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs),
        patch("src.agents.jarvis_agent.load_athlete_config", return_value={"lthr": 165}),
    ):
        result = fn(limit=5)
    assert isinstance(result, list)


def test_get_recent_runs_respects_limit(db_with_runs):
    fn = _tool_fn("get_recent_runs")
    with (
        patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs),
        patch("src.agents.jarvis_agent.load_athlete_config", return_value={"lthr": 165}),
    ):
        result_1 = fn(limit=1)
        result_5 = fn(limit=5)
    assert len(result_1) <= 1
    assert len(result_5) >= len(result_1)


def test_get_recent_runs_schema_allows_extra_fields():
    """Gemini sometimes sends extra fields — schema must not have additionalProperties: false."""
    from src.agents.jarvis_agent import agent
    tool = agent._function_toolset.tools["get_recent_runs"]
    schema = tool.tool_def.parameters_json_schema
    assert "additionalProperties" not in schema


# ── get_health_trend ──────────────────────────────────────────────────────────

def test_get_health_trend_returns_list(db_with_runs):
    fn = _tool_fn("get_health_trend")
    with patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs):
        result = fn(days=14)
    assert isinstance(result, list)


def test_get_health_trend_row_has_expected_keys(db_with_runs):
    fn = _tool_fn("get_health_trend")
    with patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs):
        result = fn(days=30)
    if result:
        row = result[0]
        for key in ("date", "resting_heart_rate", "hrv_last_night_avg", "sleep_score"):
            assert key in row, f"Missing key: {key}"


def test_get_health_trend_schema_allows_extra_fields():
    from src.agents.jarvis_agent import agent
    tool = agent._function_toolset.tools["get_health_trend"]
    schema = tool.tool_def.parameters_json_schema
    assert "additionalProperties" not in schema


# ── get_upcoming_workouts ─────────────────────────────────────────────────────

def test_get_upcoming_workouts_returns_dict():
    fn = _tool_fn("get_upcoming_workouts")
    plan = {"today_tomorrow": [], "week": []}
    with patch("src.agents.jarvis_agent.load_training_plan", return_value=plan):
        result = fn()
    assert isinstance(result, dict)


def test_get_upcoming_workouts_no_plan_returns_empty():
    fn = _tool_fn("get_upcoming_workouts")
    with patch("src.agents.jarvis_agent.load_training_plan", return_value={}):
        result = fn()
    assert result == {}


# ── query_athlete_data ────────────────────────────────────────────────────────

def test_query_athlete_data_select_returns_list(db_with_runs):
    fn = _tool_fn("query_athlete_data")
    with patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs):
        result = fn(sql="SELECT COUNT(*) AS n FROM gold_runs")
    assert isinstance(result, list)
    assert result[0]["n"] > 0


def test_query_athlete_data_rejects_non_select(db_with_runs):
    fn = _tool_fn("query_athlete_data")
    with patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs):
        result = fn(sql="DROP TABLE gold_runs")
    assert result == [{"error": "Only SELECT statements are permitted."}]


def test_query_athlete_data_handles_bad_sql(db_with_runs):
    fn = _tool_fn("query_athlete_data")
    with patch("src.agents.jarvis_agent.StarkDatabase", return_value=db_with_runs):
        result = fn(sql="SELECT * FROM nonexistent_table_xyz")
    assert len(result) == 1
    assert "error" in result[0]


def test_query_athlete_data_schema_allows_extra_fields():
    from src.agents.jarvis_agent import agent
    tool = agent._function_toolset.tools["query_athlete_data"]
    schema = tool.tool_def.parameters_json_schema
    assert "additionalProperties" not in schema
