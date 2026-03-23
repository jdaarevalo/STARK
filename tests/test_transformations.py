import json
import zipfile
import struct
import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def raw_dir(tmp_path):
    return tmp_path / "raw"


@pytest.fixture
def processed_dir(tmp_path):
    d = tmp_path / "processed"
    d.mkdir()
    return d


def write_sleep_json(raw_dir: Path, date_str: str, score: int = 85) -> None:
    raw_dir.mkdir(exist_ok=True)
    data = {
        "dailySleepDTO": {
            "calendarDate": date_str,
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 14400,
            "remSleepSeconds": 7200,
            "awakeSleepSeconds": 1800,
            "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD"}},
            "avgHeartRate": 55.0,
            "averageRespirationValue": 15.0,
            "averageSpO2Value": 96.0,
            "avgSleepStress": 18.0,
            "sleepScoreFeedback": "POSITIVE_LONG_AND_REFRESHING",
        }
    }
    (raw_dir / f"sleep_data_{date_str}.json").write_text(json.dumps(data))


# ── process_sleep_jsons ───────────────────────────────────────────────────────

def test_process_sleep_jsons_creates_parquet(raw_dir, processed_dir):
    from src.db.transformations import process_sleep_jsons

    write_sleep_json(raw_dir, "2026-03-21", score=89)

    with patch("src.db.transformations.RAW_DIR", raw_dir), \
         patch("src.db.transformations.PROCESSED_DIR", processed_dir):
        process_sleep_jsons()

    output = processed_dir / "silver_sleep_data.parquet"
    assert output.exists()
    df = pd.read_parquet(output)
    assert len(df) == 1
    assert df["sleep_score"].iloc[0] == 89


def test_process_sleep_jsons_no_files_does_not_raise(raw_dir, processed_dir):
    from src.db.transformations import process_sleep_jsons

    raw_dir.mkdir()
    with patch("src.db.transformations.RAW_DIR", raw_dir), \
         patch("src.db.transformations.PROCESSED_DIR", processed_dir):
        process_sleep_jsons()  # should not raise

    assert not (processed_dir / "silver_sleep_data.parquet").exists()


# ── process_health_telemetry_jsons ────────────────────────────────────────────

def test_process_health_telemetry_creates_parquet(raw_dir, processed_dir):
    from src.db.transformations import process_health_telemetry_jsons

    raw_dir.mkdir(exist_ok=True)
    data = {
        "date": "2026-03-21",
        "summary": {"restingHeartRate": 52, "averageStressLevel": 17,
                    "totalSteps": 8000, "activeKilocalories": 400.0,
                    "totalDistanceMeters": 6000.0},
        "hrv": {"hrvSummary": {"weeklyAvg": 37, "lastNightAvg": 38, "status": "UNBALANCED"}},
        "body_battery": [{"bodyBatteryValuesArray": [[0, 70], [1, 85]]}],
        "training_status": {"trainingStatusPhrase": None,
                            "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 43.0}}},
        "stress": {},
    }
    (raw_dir / "health_telemetry_2026-03-21.json").write_text(json.dumps(data))

    with patch("src.db.transformations.RAW_DIR", raw_dir), \
         patch("src.db.transformations.PROCESSED_DIR", processed_dir):
        process_health_telemetry_jsons()

    output = processed_dir / "silver_health_telemetry.parquet"
    assert output.exists()
    df = pd.read_parquet(output)
    assert df["hrv_last_night_avg"].iloc[0] == 38
    assert df["body_battery_end"].iloc[0] == 85
    assert df["vo2_max"].iloc[0] == 43.0
