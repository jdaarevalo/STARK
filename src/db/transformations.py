import os
import json
import zipfile
import glob
import logging
from pathlib import Path

import pandas as pd
from fitparse import FitFile

# Project root (two levels up: src/db/ -> src/ -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.db.transformations")


def process_sleep_jsons():
    """Reads sleep JSONs, extracts key metrics and saves them as Parquet."""
    logger.info("Starting sleep JSON processing...")
    json_files = glob.glob(str(RAW_DIR / "sleep_data_*.json"))

    if not json_files:
        logger.warning("No sleep files found in raw.")
        return

    records = []
    for file in json_files:
        with open(file, "r") as f:
            data = json.load(f)
            # Extract high-level metrics for J.A.R.V.I.S.
            dto = data.get("dailySleepDTO") or {}
            record = {
                "date": dto.get("calendarDate"),
                "sleep_time_seconds": dto.get("sleepTimeSeconds"),
                "deep_sleep_seconds": dto.get("deepSleepSeconds"),
                "light_sleep_seconds": dto.get("lightSleepSeconds"),
                "rem_sleep_seconds": dto.get("remSleepSeconds"),
                "awake_sleep_seconds": dto.get("awakeSleepSeconds"),
                "sleep_score": (dto.get("sleepScore") or {}).get("value"),
            }
            records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    output_path = PROCESSED_DIR / "silver_sleep_data.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} sleep records to {output_path}")


def process_health_telemetry_jsons():
    """Reads health telemetry JSONs, flattens key metrics and saves them as Parquet."""
    logger.info("Starting health telemetry JSON processing...")
    json_files = glob.glob(str(RAW_DIR / "health_telemetry_*.json"))

    if not json_files:
        logger.warning("No health telemetry files found in raw.")
        return

    records = []
    for file in json_files:
        with open(file, "r") as f:
            data = json.load(f)

        summary = data.get("summary") or {}
        hrv = data.get("hrv") or {}
        hrv_summary = hrv.get("hrvSummary") or {}
        body_battery = data.get("body_battery") or []
        training_status = data.get("training_status") or {}

        record = {
            "date": data.get("date"),
            # Summary
            "resting_heart_rate": summary.get("restingHeartRate"),
            "avg_stress_level": summary.get("averageStressLevel"),
            "steps": summary.get("totalSteps"),
            "active_calories": summary.get("activeKilocalories"),
            "total_distance_meters": summary.get("totalDistanceMeters"),
            # HRV
            "hrv_weekly_avg": hrv_summary.get("weeklyAvg"),
            "hrv_last_night_avg": hrv_summary.get("lastNight"),
            "hrv_status": hrv_summary.get("status"),
            # Body battery — take the last reading of the day
            "body_battery_end": body_battery[-1].get("value") if body_battery else None,
            # Training status
            "training_status_phrase": training_status.get("trainingStatusPhrase"),
            "vo2_max": training_status.get("mostRecentVO2Max"),
        }
        records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    output_path = PROCESSED_DIR / "silver_health_telemetry.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} health telemetry records to {output_path}")


def process_fit_files():
    """Unzips run ZIPs, parses the .FIT file and saves lap telemetry as Parquet."""
    logger.info("Starting run telemetry processing (.FIT)...")
    zip_files = glob.glob(str(RAW_DIR / "run_*.zip"))

    if not zip_files:
        logger.warning("No run ZIP files found in raw.")
        return

    for zip_path in zip_files:
        filename = os.path.basename(zip_path)
        activity_id = filename.split("_")[-1].replace(".zip", "")

        # 1. Temporarily extract the FIT file from the ZIP
        with zipfile.ZipFile(zip_path, "r") as z:
            fit_filename = z.namelist()[0]
            z.extract(fit_filename, RAW_DIR)
            extracted_fit_path = RAW_DIR / fit_filename

        # 2. Parse the binary FIT file
        logger.info(f"Parsing FIT file for activity {activity_id}...")
        fitfile = FitFile(str(extracted_fit_path))

        records = []
        # Iterate over second-by-second telemetry messages
        for record in fitfile.get_messages("record"):
            row = {data.name: data.value for data in record}
            records.append(row)

        # 3. Remove the temporary FIT file
        os.remove(extracted_fit_path)

        if not records:
            continue

        # 4. Convert to DataFrame, drop undocumented proprietary Garmin fields
        df = pd.DataFrame(records)
        unknown_cols = [c for c in df.columns if c.startswith("unknown_")]
        df = df.drop(columns=unknown_cols)
        output_path = PROCESSED_DIR / f"silver_run_{activity_id}.parquet"
        df.to_parquet(output_path, index=False)
        logger.info(f"Run telemetry saved with {len(df)} rows to {output_path}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()

    process_sleep_jsons()
    process_health_telemetry_jsons()
    process_fit_files()
    logger.info("Bronze -> Silver processing complete.")
