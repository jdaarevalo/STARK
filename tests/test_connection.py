import pytest
import pandas as pd
from unittest.mock import patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset StarkDatabase singleton between tests."""
    from src.db import connection
    connection.StarkDatabase._instance = None
    yield
    connection.StarkDatabase._instance = None


@pytest.fixture
def db_with_data(tmp_path):
    """Creates a StarkDatabase backed by real Parquet fixtures in tmp_path."""
    processed = tmp_path / "processed"
    processed.mkdir()
    # Sleep fixture
    sleep_df = pd.DataFrame([{
        "date": pd.Timestamp("2026-03-21"),
        "sleep_time_seconds": 28800,
        "deep_sleep_seconds": 5400,
        "rem_sleep_seconds": 7200,
        "sleep_score": 89,
        "sleep_score_qualifier": "GOOD",
        "avg_heart_rate": 55.0,
        "avg_stress": 18.0,
        "light_sleep_seconds": 14400,
        "awake_sleep_seconds": 1800,
        "avg_respiration": 15.0,
        "avg_spo2": 96.0,
        "sleep_score_feedback": "POSITIVE",
    }])
    sleep_df.to_parquet(processed / "silver_sleep_data.parquet", index=False)

    # Health fixture
    health_df = pd.DataFrame([{
        "date": pd.Timestamp("2026-03-21"),
        "resting_heart_rate": 52,
        "avg_stress_level": 17,
        "steps": 8000,
        "active_calories": 400.0,
        "total_distance_meters": 6000.0,
        "hrv_weekly_avg": 37,
        "hrv_last_night_avg": 38,
        "hrv_status": "UNBALANCED",
        "body_battery_end": 86,
        "training_status_phrase": None,
        "vo2_max": 43.0,
    }])
    health_df.to_parquet(processed / "silver_health_telemetry.parquet", index=False)

    # Run fixture
    run_df = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-03-21 07:00:00"),
        "distance": 6000.0,
        "heart_rate": 140,
        "cadence": 80,
        "enhanced_speed": 2.8,
    }])
    run_df.to_parquet(processed / "silver_run_12345.parquet", index=False)

    with patch("src.db.connection.PROCESSED_DIR", processed):
        from src.db.connection import StarkDatabase
        db = StarkDatabase()
        yield db
        db.close()


# ── StarkDatabase ─────────────────────────────────────────────────────────────

def test_singleton_returns_same_instance(db_with_data):
    from src.db.connection import StarkDatabase
    db2 = StarkDatabase()
    assert db_with_data is db2


def test_get_daily_readiness_returns_data(db_with_data):
    result = db_with_data.get_daily_readiness("2026-03-21")
    assert result is not None
    assert result["sleep_score"] == 89
    assert result["hrv_last_night_avg"] == 38
    assert result["body_battery_end"] == 86


def test_get_daily_readiness_missing_date_returns_none(db_with_data):
    result = db_with_data.get_daily_readiness("2000-01-01")
    assert result is None


def test_get_run_summary_returns_data(db_with_data):
    result = db_with_data.get_run_summary("12345")
    assert result is not None
    assert result["total_distance_meters"] == 6000.0
    assert result["avg_heart_rate"] == 140.0


def test_get_run_summary_missing_activity_returns_none(db_with_data):
    result = db_with_data.get_run_summary("99999")
    assert result is None
