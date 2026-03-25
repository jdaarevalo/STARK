import os
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv
from garminconnect import Garmin

# Project root (two levels up from this file: src/extractors/ -> src/ -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.extractors.garmin")


def init_garmin_client():
    """Initializes the Garmin client and manages the session to avoid lockouts."""
    load_dotenv()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    # Directory where OAuth session tokens are stored (garth format)
    tokenstore = os.path.expanduser("~/.garminconnect")

    client = Garmin(email, password)
    tokenstore_path = Path(tokenstore)

    # Only attempt token-based login if token files exist and are non-empty
    has_tokens = (
        tokenstore_path.is_dir()
        and any(f.stat().st_size > 0 for f in tokenstore_path.glob("*.json"))
    )

    if has_tokens:
        try:
            logger.info("Attempting to load saved session...")
            client.login(tokenstore=tokenstore)
            logger.info("Login successful using saved tokens!")
            return client
        except Exception as e:
            logger.warning(f"Saved session invalid ({e}). Falling back to fresh login...")

    logger.info("Starting fresh login...")
    client.login()
    tokenstore_path.mkdir(parents=True, exist_ok=True)
    client.garth.dump(tokenstore)
    logger.info(f"Fresh login successful. Tokens saved to {tokenstore}")

    return client


def extract_sleep_data(client, target_date):
    """Extracts and saves sleep data for a specific date."""
    logger.info(f"Extracting sleep data for {target_date}...")
    try:
        sleep_data = client.get_sleep_data(target_date.isoformat())

        # Save raw JSON to the local Data Lake
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        filename = RAW_DATA_DIR / f"sleep_data_{target_date}.json"
        with open(filename, "w") as f:
            json.dump(sleep_data, f, indent=4)
        logger.info(f"Sleep data saved to {filename}")

    except Exception as e:
        logger.error(f"Error extracting sleep data: {e}")


def extract_daily_health_summary(client, target_date):
    """Extracts key physiological metrics for the day."""
    date_str = target_date.isoformat()
    logger.info(f"Extracting health telemetry for {date_str}...")

    daily_data = {
        "date": date_str,
        "summary": client.get_user_summary(date_str),
        "body_battery": client.get_body_battery(date_str),
        "hrv": client.get_hrv_data(date_str),
        "stress": client.get_all_day_stress(date_str),
        "training_status": client.get_training_status(date_str),
    }

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = RAW_DATA_DIR / f"health_telemetry_{date_str}.json"
    with open(filename, "w") as f:
        json.dump(daily_data, f, indent=4)
    logger.info(f"Health telemetry saved to {filename}")


def get_last_extracted_date() -> date:
    """
    Returns the most recent date for which sleep data was already extracted.
    Falls back to yesterday if no files exist yet.
    """
    existing = sorted(RAW_DATA_DIR.glob("sleep_data_*.json"))
    if not existing:
        return date.today() - timedelta(days=1)
    latest_filename = existing[-1].stem  # e.g. "sleep_data_2026-03-23"
    date_str = latest_filename.replace("sleep_data_", "")
    return date.fromisoformat(date_str)


def extract_runs_in_range(client, start_date: date, end_date: date) -> None:
    """
    Downloads .FIT files for all runs between start_date and end_date (inclusive).
    Skips activities already present in data/raw/.
    """
    logger.info(f"Scanning runs from {start_date} to {end_date}...")
    try:
        # Fetch enough activities to cover the date range (50 is a safe ceiling)
        activities = client.get_activities(0, 50)
        runs = [
            act for act in activities
            if act.get("activityType", {}).get("typeKey") == "running"
            and start_date <= date.fromisoformat(act["startTimeLocal"][:10]) <= end_date
        ]

        if not runs:
            logger.info("No runs found in the specified date range.")
            return

        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        for run in runs:
            activity_id = run["activityId"]
            start_time = run["startTimeLocal"][:10]
            filename = RAW_DATA_DIR / f"run_{start_time}_{activity_id}.zip"

            if filename.exists():
                logger.info(f"Skipping run {activity_id} on {start_time} — already downloaded.")
                continue

            logger.info(f"Downloading .FIT for '{run.get('activityName', 'Run')}' on {start_time} (ID: {activity_id})...")
            fit_data = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)
            with open(filename, "wb") as f:
                f.write(fit_data)
            logger.info(f"Saved {filename}")

    except Exception as e:
        logger.error(f"Error extracting runs in range: {e}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()

    client = init_garmin_client()

    today = date.today()
    last_extracted = get_last_extracted_date()
    logger.info(f"Extracting data from {last_extracted} to {today} (inclusive)...")

    # Extract sleep + health for each missing day
    current = last_extracted
    while current <= today:
        sleep_file = RAW_DATA_DIR / f"sleep_data_{current}.json"
        if not sleep_file.exists():
            extract_sleep_data(client, current)
        else:
            logger.info(f"Skipping sleep data for {current} — already extracted.")

        health_file = RAW_DATA_DIR / f"health_telemetry_{current}.json"
        if not health_file.exists():
            extract_daily_health_summary(client, current)
        else:
            logger.info(f"Skipping health telemetry for {current} — already extracted.")

        current += timedelta(days=1)

    # Extract all runs in the same range
    extract_runs_in_range(client, last_extracted, today)

    logger.info("Extraction pipeline finished.")