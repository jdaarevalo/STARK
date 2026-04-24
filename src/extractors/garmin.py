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


def init_garmin_client() -> Garmin:
    """Returns an authenticated Garmin client using saved DI tokens.

    Tokens must exist at ~/.garminconnect — run `uv run python scripts/garmin_auth.py` first.
    """
    load_dotenv()
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    tokenstore = os.path.expanduser("~/.garminconnect")
    tokenstore_path = Path(tokenstore)

    has_tokens = (
        tokenstore_path.is_dir()
        and any(f.stat().st_size > 0 for f in tokenstore_path.glob("*.json"))
    )

    if not has_tokens:
        raise RuntimeError(
            "No Garmin tokens found. Run `uv run python scripts/garmin_auth.py` first."
        )

    client = Garmin(email, password)
    try:
        logger.info("Loading saved Garmin tokens...")
        client.login(tokenstore=tokenstore)
        logger.info("Authenticated successfully.")
    except Exception as e:
        raise RuntimeError(
            f"Failed to authenticate with saved tokens ({e}). "
            "Re-run `uv run python scripts/garmin_auth.py` to refresh them."
        ) from e

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


def extract_weight_history(client, days: int = 30) -> None:
    """Extracts weigh-in history for the last N days from Garmin Connect."""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    logger.info(f"Extracting weight history from {start} to {end}...")
    try:
        data = client.get_weigh_ins(start, end)
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        filename = RAW_DATA_DIR / f"weight_history_{today}.json"
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Weight history saved to {filename}")
    except Exception as e:
        logger.error(f"Error extracting weight history: {e}")


def extract_training_plan(client) -> None:
    """Extracts the active Garmin Coach adaptive training plan."""
    logger.info("Extracting Garmin Coach training plan...")
    try:
        plans = client.get_training_plans()
        plan_list = plans.get("trainingPlanList") or []
        # Pick the most recently created active plan
        active = sorted(
            [p for p in plan_list if p.get("trainingStatus", {}).get("statusKey") == "Scheduled"],
            key=lambda p: p.get("createDate", ""),
            reverse=True,
        )
        if not active:
            logger.warning("No active Garmin Coach plan found.")
            return
        plan_id = active[0]["trainingPlanId"]
        data = client.get_adaptive_training_plan_by_id(plan_id)
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        filename = RAW_DATA_DIR / f"training_plan_{today}.json"
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Training plan saved to {filename}")
    except Exception as e:
        logger.error(f"Error extracting training plan: {e}")


def extract_race_predictions(client) -> None:
    """Extracts Garmin's current race time predictions (5K, 10K, HM, Marathon)."""
    logger.info("Extracting race predictions...")
    try:
        data = client.get_race_predictions()
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        filename = RAW_DATA_DIR / f"race_predictions_{today}.json"
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Race predictions saved to {filename}")
    except Exception as e:
        logger.error(f"Error extracting race predictions: {e}")


def extract_lactate_threshold(client) -> None:
    """Fetches Garmin's latest device-estimated LTHR and updates athlete_config.json.

    Only writes if a valid bpm value is found and it differs from the stored value.
    Non-fatal: logs a warning and returns if Garmin has no estimate yet.
    """
    logger.info("Extracting lactate threshold data from Garmin...")
    try:
        data = client.get_lactate_threshold(latest=True)

        entry = data[0] if isinstance(data, list) else (data or {})

        # Garmin may nest the value differently depending on device/firmware
        lthr_bpm = (
            entry.get("heartRateIntervalBpm")
            or entry.get("lactateThresholdHeartRate")
            or ((entry.get("heartRate") or {}).get("value"))
        )

        if not lthr_bpm or not isinstance(lthr_bpm, (int, float)):
            logger.warning(f"No valid LTHR bpm found in Garmin response: {entry}")
            return

        lthr_bpm = int(lthr_bpm)

        config_path = PROJECT_ROOT / "data" / "athlete_config.json"
        config: dict = {}
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)

        current = config.get("lthr", 0)
        if current == lthr_bpm:
            logger.info(f"LTHR unchanged at {lthr_bpm} bpm — skipping update.")
            return

        config["lthr"] = lthr_bpm
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        logger.info(f"LTHR updated: {current} → {lthr_bpm} bpm (athlete_config.json)")

    except Exception as e:
        logger.error(f"Error extracting lactate threshold: {e}")


def extract_hydration(client, target_date) -> None:
    """Extracts daily hydration intake from Garmin Connect."""
    date_str = target_date.isoformat()
    logger.info(f"Extracting hydration data for {date_str}...")
    try:
        data = client.get_hydration_data(date_str)
        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
        filename = RAW_DATA_DIR / f"hydration_{date_str}.json"
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
        logger.info(f"Hydration data saved to {filename}")
    except Exception as e:
        logger.error(f"Error extracting hydration data for {date_str}: {e}")


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


def run_full_extraction() -> None:
    """
    Full extraction pipeline: authenticates with Garmin and downloads all missing
    sleep, health telemetry, and .FIT run files up to today.

    Raises RuntimeError if authentication fails (no tokens or expired tokens).
    """
    client = init_garmin_client()

    today = date.today()
    last_extracted = get_last_extracted_date()
    logger.info(f"Extracting data from {last_extracted} to {today} (inclusive)...")

    # Sleep and health: re-fetch today and yesterday (may be incomplete until end of day).
    # Hydration: re-fetch last 7 days — users log water retroactively throughout the day
    # and Garmin's sync delay means earlier days can still update after the fact.
    biometric_refresh_cutoff = today - timedelta(days=1)
    hydration_refresh_cutoff = today - timedelta(days=7)

    current = last_extracted
    while current <= today:
        sleep_file = RAW_DATA_DIR / f"sleep_data_{current}.json"
        if not sleep_file.exists() or current >= biometric_refresh_cutoff:
            extract_sleep_data(client, current)
        else:
            logger.info(f"Skipping sleep data for {current} — already extracted.")

        health_file = RAW_DATA_DIR / f"health_telemetry_{current}.json"
        if not health_file.exists() or current >= biometric_refresh_cutoff:
            extract_daily_health_summary(client, current)
        else:
            logger.info(f"Skipping health telemetry for {current} — already extracted.")

        hydration_file = RAW_DATA_DIR / f"hydration_{current}.json"
        if not hydration_file.exists() or current >= hydration_refresh_cutoff:
            extract_hydration(client, current)
        else:
            logger.info(f"Skipping hydration for {current} — already extracted.")

        current += timedelta(days=1)

    extract_runs_in_range(client, last_extracted, today)
    extract_training_plan(client)
    extract_race_predictions(client)
    extract_weight_history(client, days=30)
    extract_lactate_threshold(client)
    logger.info("Extraction pipeline finished.")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()
    run_full_extraction()