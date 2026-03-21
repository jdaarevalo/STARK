import os
import json
import logging
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from garminconnect import Garmin, GarminConnectConnectionError, GarminConnectAuthenticationError

# Project root (two levels up from this file: src/extractors/ -> src/ -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.extractors.garmin")


def init_garmin_client():
    """Initializes the Garmin client and manages the session to avoid lockouts."""
    load_dotenv()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    # Directory where OAuth session tokens are stored
    tokenstore = os.path.expanduser("~/.garminconnect")

    try:
        logger.info("Attempting to load saved session...")
        client = Garmin(email, password, session_data=tokenstore)
        client.login()
        logger.info("Login successful using saved tokens!")
    except (GarminConnectAuthenticationError, Exception) as e:
        logger.warning("Could not use saved session. Starting fresh login...")
        # Falls back to full login (e.g. first run or expired tokens)
        client = Garmin(email, password)
        client.login()
        # Persist the session for next run — garminconnect uses garth under the hood
        import garth
        garth.client.dump(tokenstore)
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


def extract_latest_run_fit(client):
    """Fetches the latest run and downloads its original .FIT file."""
    logger.info("Looking for the latest activity...")
    try:
        # Fetch the last 5 activities to find the latest run
        activities = client.get_activities(0, 5)

        # Filter only running activities
        runs = [act for act in activities if act.get('activityType', {}).get('typeKey') == 'running']

        if not runs:
            logger.warning("No recent runs found.")
            return

        latest_run = runs[0]
        activity_id = latest_run['activityId']
        activity_name = latest_run.get('activityName', 'Run')
        start_time = latest_run['startTimeLocal'][:10]  # YYYY-MM-DD

        logger.info(f"Latest run found: '{activity_name}' on {start_time} (ID: {activity_id})")

        # Download the original FIT file
        logger.info(f"Downloading .FIT file for activity {activity_id}...")
        fit_data = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.ORIGINAL)

        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        filename = RAW_DATA_DIR / f"run_{start_time}_{activity_id}.zip"  # Garmin returns a ZIP containing the FIT
        with open(filename, "wb") as f:
            f.write(fit_data)
        logger.info(f"Raw file saved as {filename}")

    except Exception as e:
        logger.error(f"Error extracting activity: {e}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()

    # 1. Authenticate
    client = init_garmin_client()

    # 2. Extract today's sleep data
    today = date.today()
    extract_sleep_data(client, today)

    # 3. Extract the latest run
    extract_latest_run_fit(client)

    logger.info("Extraction pipeline finished.")