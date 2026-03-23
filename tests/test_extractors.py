import json
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_garmin_client(sleep_data=None, activities=None, fit_bytes=b"FIT"):
    client = MagicMock()
    client.get_sleep_data.return_value = sleep_data or {}
    client.get_activities.return_value = activities or []
    client.download_activity.return_value = fit_bytes
    client.ActivityDownloadFormat.ORIGINAL = "original"
    return client


# ── extract_sleep_data ────────────────────────────────────────────────────────

def test_extract_sleep_data_writes_json(tmp_path):
    from src.extractors.garmin import extract_sleep_data

    client = make_garmin_client(sleep_data={"calendarDate": "2026-03-21", "dailySleepDTO": {}})

    with patch("src.extractors.garmin.RAW_DATA_DIR", tmp_path):
        extract_sleep_data(client, date(2026, 3, 21))

    output = tmp_path / "sleep_data_2026-03-21.json"
    assert output.exists()
    data = json.loads(output.read_text())
    assert data["calendarDate"] == "2026-03-21"


def test_extract_sleep_data_handles_api_error(tmp_path, caplog):
    from src.extractors.garmin import extract_sleep_data

    client = MagicMock()
    client.get_sleep_data.side_effect = Exception("API error")

    with patch("src.extractors.garmin.RAW_DATA_DIR", tmp_path):
        extract_sleep_data(client, date(2026, 3, 21))  # should not raise

    assert not any(tmp_path.iterdir())


# ── extract_latest_run_fit ────────────────────────────────────────────────────

def test_extract_latest_run_fit_writes_zip(tmp_path):
    from src.extractors.garmin import extract_latest_run_fit

    activities = [{
        "activityType": {"typeKey": "running"},
        "activityId": 12345,
        "activityName": "Morning Run",
        "startTimeLocal": "2026-03-21T07:00:00",
    }]
    client = make_garmin_client(activities=activities, fit_bytes=b"FITDATA")

    with patch("src.extractors.garmin.RAW_DATA_DIR", tmp_path):
        extract_latest_run_fit(client)

    output = tmp_path / "run_2026-03-21_12345.zip"
    assert output.exists()
    assert output.read_bytes() == b"FITDATA"


def test_extract_latest_run_fit_no_runs_logs_warning(tmp_path, caplog):
    from src.extractors.garmin import extract_latest_run_fit

    client = make_garmin_client(activities=[
        {"activityType": {"typeKey": "cycling"}, "activityId": 99}
    ])

    with patch("src.extractors.garmin.RAW_DATA_DIR", tmp_path):
        extract_latest_run_fit(client)

    assert not any(tmp_path.iterdir())


# ── extract_daily_health_summary ──────────────────────────────────────────────

def test_extract_daily_health_summary_writes_json(tmp_path):
    from src.extractors.garmin import extract_daily_health_summary

    client = MagicMock()
    client.get_user_summary.return_value = {"restingHeartRate": 52}
    client.get_body_battery.return_value = []
    client.get_hrv_data.return_value = {}
    client.get_all_day_stress.return_value = {}
    client.get_training_status.return_value = {}

    with patch("src.extractors.garmin.RAW_DATA_DIR", tmp_path):
        extract_daily_health_summary(client, date(2026, 3, 21))

    output = tmp_path / "health_telemetry_2026-03-21.json"
    assert output.exists()
    data = json.loads(output.read_text())
    assert data["date"] == "2026-03-21"
    assert data["summary"]["restingHeartRate"] == 52
