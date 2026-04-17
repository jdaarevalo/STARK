"""
Tests for the new StarkDatabase query methods and JSON persistence utilities
added during dashboard implementation (Phases 1-4).

Covered:
  - get_weekly_intensity
  - get_efficiency_trend
  - get_training_load_history
  - get_km_since
  - load/save_athlete_config
  - load/save_daily_input
"""
import pandas as pd
import pytest
from unittest.mock import patch


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
    """
    StarkDatabase backed by two run Parquet fixtures and minimal sleep/health.
    Run A: easy (HR < 145 throughout), 10 km, 2026-03-21.
    Run B: hard (HR > 160 throughout), 8 km, 2026-03-28.
    """
    processed = tmp_path / "processed"
    processed.mkdir()

    # Minimal sleep + health so views don't fail on LEFT JOINs
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

    # Run A — easy, 60 records spanning 1 hour, HR=135, speed=2.8 m/s (~5:57/km)
    run_a_rows = [
        {
            "timestamp": pd.Timestamp("2026-03-21 07:00:00") + pd.Timedelta(minutes=i),
            "distance": float(i * 167),      # ~10 km total
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
    _make_run_parquet(processed / "silver_run_easy_001.parquet", run_a_rows)

    # Run B — hard, 48 records, HR=165, speed=3.5 m/s (~4:46/km)
    run_b_rows = [
        {
            "timestamp": pd.Timestamp("2026-03-28 07:00:00") + pd.Timedelta(minutes=i),
            "distance": float(i * 175),
            "heart_rate": 165,
            "cadence": 88,
            "enhanced_speed": 3.5,
            "power": 310.0,
            "step_length": 1250.0,
            "vertical_oscillation": 90.0,
            "vertical_ratio": 7.8,
            "stance_time": 230.0,
            "temperature": 20.0,
            "enhanced_altitude": 105.0,
        }
        for i in range(48)
    ]
    _make_run_parquet(processed / "silver_run_hard_002.parquet", run_b_rows)

    with patch("src.db.connection.PROCESSED_DIR", processed):
        from src.db.connection import StarkDatabase
        db = StarkDatabase()
        yield db
        db.close()


# ── get_weekly_intensity ───────────────────────────────────────────────────────

def test_get_weekly_intensity_returns_list(db_with_runs):
    result = db_with_runs.get_weekly_intensity(weeks=4)
    assert isinstance(result, list)
    assert len(result) >= 1


def test_get_weekly_intensity_columns(db_with_runs):
    result = db_with_runs.get_weekly_intensity(weeks=4)
    row = result[0]
    for col in ("week_start", "z1_min", "z2_min", "z3_min", "z4_min", "z5_min", "total_min"):
        assert col in row, f"Missing column: {col}"


def test_get_weekly_intensity_total_equals_sum_of_zones(db_with_runs):
    result = db_with_runs.get_weekly_intensity(weeks=4)
    for row in result:
        zone_sum = row["z1_min"] + row["z2_min"] + row["z3_min"] + row["z4_min"] + row["z5_min"]
        assert abs(zone_sum - row["total_min"]) < 0.01


def test_get_weekly_intensity_easy_run_lands_in_z1_z2(db_with_runs):
    # Run A has HR=135, which is Z2 (125-144). Its minutes should appear in z1+z2.
    result = db_with_runs.get_weekly_intensity(weeks=4)
    # Find the week containing 2026-03-21
    week_21 = next(
        (r for r in result if "2026-03-16" <= str(r["week_start"])[:10] <= "2026-03-22"),
        None,
    )
    assert week_21 is not None
    assert week_21["z2_min"] > 0
    assert week_21["z4_min"] == 0.0


def test_get_weekly_intensity_hard_run_lands_in_z4_z5(db_with_runs):
    # Run B has HR=165, which is Z4 (160-174).
    result = db_with_runs.get_weekly_intensity(weeks=4)
    week_28 = next(
        (r for r in result if "2026-03-23" <= str(r["week_start"])[:10] <= "2026-03-29"),
        None,
    )
    assert week_28 is not None
    assert week_28["z4_min"] > 0
    assert week_28["z2_min"] == 0.0


def test_get_weekly_intensity_empty_db_returns_empty_list(tmp_path):
    from src.db import connection
    connection.StarkDatabase._instance = None

    processed = tmp_path / "processed"
    processed.mkdir()
    # DuckDB requires at least one matching file for the silver_run_*.parquet glob.
    # Write minimal valid parquets — no rows, but correct enough for views to be created.
    pd.DataFrame(columns=["date"]).to_parquet(processed / "silver_sleep_data.parquet", index=False)
    pd.DataFrame(columns=["date"]).to_parquet(processed / "silver_health_telemetry.parquet", index=False)
    pd.DataFrame(columns=["timestamp", "heart_rate", "distance", "enhanced_speed"]).to_parquet(
        processed / "silver_run_empty.parquet", index=False
    )

    with patch("src.db.connection.PROCESSED_DIR", processed):
        from src.db.connection import StarkDatabase
        db = StarkDatabase()
        result = db.get_weekly_intensity(weeks=4)
        db.close()

    assert result == []


# ── get_efficiency_trend ──────────────────────────────────────────────────────

def test_get_efficiency_trend_returns_list(db_with_runs):
    result = db_with_runs.get_efficiency_trend(weeks=16)
    assert isinstance(result, list)


def test_get_efficiency_trend_easy_run_included(db_with_runs):
    # Run A (HR=135) has easy_fraction=1.0 >= 0.70 → must appear
    result = db_with_runs.get_efficiency_trend(weeks=16)
    assert len(result) >= 1


def test_get_efficiency_trend_hard_run_excluded(db_with_runs):
    # Run B (HR=165) has easy_fraction=0.0 < 0.70 → must NOT appear
    # With both runs, we should have exactly 1 week (the easy run week)
    result = db_with_runs.get_efficiency_trend(weeks=16)
    # Only the week of the easy run should be present
    week_starts = [str(r["week_start"])[:10] for r in result]
    assert not any("2026-03-2" in w and w >= "2026-03-23" for w in week_starts)


def test_get_efficiency_trend_columns(db_with_runs):
    result = db_with_runs.get_efficiency_trend(weeks=16)
    if result:
        row = result[0]
        for col in ("week_start", "avg_pace_sec_km", "avg_hr", "run_count", "total_km"):
            assert col in row, f"Missing column: {col}"


def test_get_efficiency_trend_pace_is_positive(db_with_runs):
    result = db_with_runs.get_efficiency_trend(weeks=16)
    for row in result:
        assert row["avg_pace_sec_km"] > 0


def test_get_efficiency_trend_hr_in_plausible_range(db_with_runs):
    result = db_with_runs.get_efficiency_trend(weeks=16)
    for row in result:
        assert 50 < row["avg_hr"] < 200


# ── get_training_load_history ─────────────────────────────────────────────────

def test_get_training_load_history_returns_list(db_with_runs):
    result = db_with_runs.get_training_load_history(days=42)
    assert isinstance(result, list)
    assert len(result) >= 1


def test_get_training_load_history_columns(db_with_runs):
    result = db_with_runs.get_training_load_history(days=42)
    row = result[0]
    for col in ("run_date", "duration_sec", "avg_hr", "hr_tss_numerator"):
        assert col in row, f"Missing column: {col}"


def test_get_training_load_history_duration_positive(db_with_runs):
    result = db_with_runs.get_training_load_history(days=42)
    for row in result:
        assert row["duration_sec"] > 0


def test_get_training_load_history_hr_tss_numerator_positive(db_with_runs):
    result = db_with_runs.get_training_load_history(days=42)
    for row in result:
        assert row["hr_tss_numerator"] > 0


def test_get_training_load_history_excludes_old_runs(db_with_runs):
    # days=1 should only return runs from the last day — fixture runs are older
    result = db_with_runs.get_training_load_history(days=1)
    assert result == []


# ── get_km_since ──────────────────────────────────────────────────────────────

def test_get_km_since_returns_float(db_with_runs):
    result = db_with_runs.get_km_since("2026-01-01")
    assert isinstance(result, float)


def test_get_km_since_includes_both_runs(db_with_runs):
    # Run A: ~10 km, Run B: ~8.4 km → total ≈ 18 km
    result = db_with_runs.get_km_since("2026-01-01")
    assert result > 15.0


def test_get_km_since_future_date_returns_zero(db_with_runs):
    result = db_with_runs.get_km_since("2099-01-01")
    assert result == 0.0


def test_get_km_since_filters_by_date(db_with_runs):
    # Only run A (Mar 21) should be included when start_date is Mar 22+
    after_run_a = db_with_runs.get_km_since("2026-03-22")
    all_runs = db_with_runs.get_km_since("2026-01-01")
    assert after_run_a < all_runs


# ── JSON persistence: athlete_config ─────────────────────────────────────────

def test_save_and_load_athlete_config(tmp_path):
    from src.db.connection import save_athlete_config, load_athlete_config

    config = {
        "target_race_date": "2026-10-15",
        "target_pace_min_per_km": 4.917,
        "lthr": 168,
        "shoes": [{"name": "Vaporfly", "start_date": "2026-01-01", "max_km": 500}],
    }
    with patch("src.db.connection._ATHLETE_CONFIG_PATH", tmp_path / "athlete_config.json"):
        save_athlete_config(config)
        loaded = load_athlete_config()

    assert loaded["target_race_date"] == "2026-10-15"
    assert loaded["lthr"] == 168
    assert loaded["shoes"][0]["name"] == "Vaporfly"


def test_load_athlete_config_missing_file_returns_empty(tmp_path):
    from src.db.connection import load_athlete_config

    with patch("src.db.connection._ATHLETE_CONFIG_PATH", tmp_path / "nonexistent.json"):
        result = load_athlete_config()

    assert result == {}


def test_save_athlete_config_creates_parent_dirs(tmp_path):
    from src.db.connection import save_athlete_config

    deep_path = tmp_path / "a" / "b" / "c" / "athlete_config.json"
    with patch("src.db.connection._ATHLETE_CONFIG_PATH", deep_path):
        save_athlete_config({"lthr": 165})

    assert deep_path.exists()


# ── JSON persistence: daily_input ─────────────────────────────────────────────

def test_save_and_load_daily_input(tmp_path):
    from src.db.connection import save_daily_input, load_daily_inputs

    with patch("src.db.connection._DAILY_INPUT_PATH", tmp_path / "daily_inputs.json"):
        save_daily_input("2026-04-16", soreness=7)
        inputs = load_daily_inputs()

    assert inputs["2026-04-16"]["soreness"] == 7


def test_save_daily_input_overwrites_same_date(tmp_path):
    from src.db.connection import save_daily_input, load_daily_inputs

    path = tmp_path / "daily_inputs.json"
    with patch("src.db.connection._DAILY_INPUT_PATH", path):
        save_daily_input("2026-04-16", soreness=3)
        save_daily_input("2026-04-16", soreness=8)
        inputs = load_daily_inputs()

    assert inputs["2026-04-16"]["soreness"] == 8


def test_save_daily_input_preserves_other_dates(tmp_path):
    from src.db.connection import save_daily_input, load_daily_inputs

    path = tmp_path / "daily_inputs.json"
    with patch("src.db.connection._DAILY_INPUT_PATH", path):
        save_daily_input("2026-04-15", soreness=2)
        save_daily_input("2026-04-16", soreness=5)
        inputs = load_daily_inputs()

    assert inputs["2026-04-15"]["soreness"] == 2
    assert inputs["2026-04-16"]["soreness"] == 5


def test_load_daily_inputs_missing_file_returns_empty(tmp_path):
    from src.db.connection import load_daily_inputs

    with patch("src.db.connection._DAILY_INPUT_PATH", tmp_path / "nonexistent.json"):
        result = load_daily_inputs()

    assert result == {}
