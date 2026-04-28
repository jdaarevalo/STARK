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
            scores = dto.get("sleepScores") or {}
            record = {
                "date": dto.get("calendarDate"),
                "sleep_time_seconds": dto.get("sleepTimeSeconds"),
                "deep_sleep_seconds": dto.get("deepSleepSeconds"),
                "light_sleep_seconds": dto.get("lightSleepSeconds"),
                "rem_sleep_seconds": dto.get("remSleepSeconds"),
                "awake_sleep_seconds": dto.get("awakeSleepSeconds"),
                "sleep_score": (scores.get("overall") or {}).get("value"),
                "sleep_score_qualifier": (scores.get("overall") or {}).get("qualifierKey"),
                "avg_heart_rate": dto.get("avgHeartRate"),
                "avg_respiration": dto.get("averageRespirationValue"),
                "avg_spo2": dto.get("averageSpO2Value"),
                "avg_stress": dto.get("avgSleepStress"),
                "sleep_score_feedback": dto.get("sleepScoreFeedback"),
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
            "hrv_last_night_avg": hrv_summary.get("lastNightAvg"),
            "hrv_status": hrv_summary.get("status"),
            # Body battery — last value in bodyBatteryValuesArray is [timestamp, level]
            "body_battery_end": (body_battery[0]["bodyBatteryValuesArray"][-1][1]
                                 if body_battery and body_battery[0].get("bodyBatteryValuesArray")
                                 else None),
            # Training status
            "training_status_phrase": training_status.get("trainingStatusPhrase"),
            "vo2_max": (training_status.get("mostRecentVO2Max") or {}).get("generic", {}).get("vo2MaxPreciseValue"),
        }
        records.append(record)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    output_path = PROCESSED_DIR / "silver_health_telemetry.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} health telemetry records to {output_path}")


def process_hydration_jsons():
    """Reads hydration JSONs and saves them as a single consolidated Parquet file."""
    logger.info("Starting hydration JSON processing...")
    json_files = glob.glob(str(RAW_DIR / "hydration_*.json"))

    if not json_files:
        logger.warning("No hydration files found in raw.")
        return

    records = []
    for file in json_files:
        with open(file, "r") as f:
            data = json.load(f)
        if not data or not data.get("calendarDate"):
            continue
        records.append({
            "date": data.get("calendarDate"),
            "intake_ml": data.get("valueInML"),
            "goal_ml": data.get("goalInML"),
            "sweat_loss_ml": data.get("sweatLossInML"),
            "activity_intake_ml": data.get("activityIntakeInML"),
            "last_entry_local": data.get("lastEntryTimestampLocal"),
        })

    if not records:
        logger.warning("No valid hydration records to process.")
        return

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])

    output_path = PROCESSED_DIR / "silver_hydration.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} hydration records to {output_path}")


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

        output_path = PROCESSED_DIR / f"silver_run_{activity_id}.parquet"
        if output_path.exists():
            logger.info(f"Skipping run {activity_id} — already processed.")
            continue

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
        df.to_parquet(output_path, index=False)
        logger.info(f"Run telemetry saved with {len(df)} rows to {output_path}")


def process_strength_jsons():
    """
    Reads strength_training_*.json and strength_sets_*.json files and saves two Silver
    Parquet files:
      - silver_strength_sessions.parquet — one row per session
      - silver_strength_sets.parquet     — one row per active exercise set
    """
    logger.info("Starting strength sessions processing...")

    # ── Sessions ──────────────────────────────────────────────────────────────
    session_files = glob.glob(str(RAW_DIR / "strength_training_*.json"))
    if session_files:
        sessions = []
        for file in session_files:
            with open(file) as f:
                data = json.load(f)
            total_zone_secs = sum(
                data.get(f"hr_time_in_zone_{z}") or 0 for z in range(1, 6)
            )
            sessions.append({
                "activity_id": str(data.get("activity_id")),
                "date": data.get("date"),
                "name": data.get("name"),
                "duration_min": round((data.get("duration_sec") or 0) / 60, 1),
                "active_min": round((data.get("moving_duration_sec") or 0) / 60, 1),
                "calories": data.get("calories"),
                "avg_hr": data.get("avg_hr"),
                "max_hr": data.get("max_hr"),
                "total_sets": data.get("total_sets"),
                "active_sets": data.get("active_sets"),
                "total_reps": data.get("total_reps"),
                "training_load": data.get("training_load"),
                "body_battery_drain": data.get("body_battery_drain"),
                "aerobic_te": data.get("aerobic_te"),
                "anaerobic_te": data.get("anaerobic_te"),
                "training_effect_label": data.get("training_effect_label"),
                "pct_z1": round(100.0 * (data.get("hr_time_in_zone_1") or 0) / total_zone_secs, 1) if total_zone_secs else None,
                "pct_z2": round(100.0 * (data.get("hr_time_in_zone_2") or 0) / total_zone_secs, 1) if total_zone_secs else None,
                "pct_z3": round(100.0 * (data.get("hr_time_in_zone_3") or 0) / total_zone_secs, 1) if total_zone_secs else None,
                "pct_z4": round(100.0 * (data.get("hr_time_in_zone_4") or 0) / total_zone_secs, 1) if total_zone_secs else None,
                "pct_z5": round(100.0 * (data.get("hr_time_in_zone_5") or 0) / total_zone_secs, 1) if total_zone_secs else None,
            })
        df = pd.DataFrame(sessions)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        out = PROCESSED_DIR / "silver_strength_sessions.parquet"
        df.to_parquet(out, index=False)
        logger.info(f"Saved {len(df)} strength sessions to {out}")
    else:
        logger.warning("No strength session files found in raw.")

    # ── Sets ──────────────────────────────────────────────────────────────────
    sets_files = glob.glob(str(RAW_DIR / "strength_sets_*.json"))
    if sets_files:
        sets_rows = []
        for file in sets_files:
            with open(file) as f:
                data = json.load(f)
            activity_id = str(data.get("activity_id"))
            act_date = data.get("date")
            for s in data.get("exercise_sets", []):
                if s.get("setType") != "ACTIVE":
                    continue
                exercises = s.get("exercises") or []
                # De-duplicate: the watch logs one entry per rep detected; take the first unique name
                seen = set()
                unique_exercises = []
                for ex in exercises:
                    key = (ex.get("category"), ex.get("name"))
                    if key not in seen:
                        seen.add(key)
                        unique_exercises.append(ex)
                for ex in unique_exercises:
                    sets_rows.append({
                        "activity_id": activity_id,
                        "date": act_date,
                        "set_start_time": s.get("startTime"),
                        "exercise_category": ex.get("category"),
                        "exercise_name": ex.get("name"),
                        "reps": s.get("repetitionCount"),
                        "weight_kg": s.get("weight"),
                        "duration_sec": s.get("duration"),
                    })
        if sets_rows:
            df = pd.DataFrame(sets_rows)
            df["date"] = pd.to_datetime(df["date"])
            df["set_start_time"] = pd.to_datetime(df["set_start_time"], errors="coerce")
            df = df.sort_values(["date", "set_start_time"])
            out = PROCESSED_DIR / "silver_strength_sets.parquet"
            df.to_parquet(out, index=False)
            logger.info(f"Saved {len(df)} strength sets to {out}")
        else:
            logger.warning("No active exercise sets found in strength_sets files.")
    else:
        logger.warning("No strength sets files found in raw.")


def process_hr_zones_jsons():
    """
    Reads hr_zones_{date}_{activity_id}.json files extracted from Garmin's hrTimeInZones
    endpoint and saves consolidated Silver Parquet with Garmin's own zone percentages
    and bpm boundaries per activity.
    """
    logger.info("Starting HR zones JSON processing...")
    json_files = glob.glob(str(RAW_DIR / "hr_zones_*.json"))

    if not json_files:
        logger.warning("No HR zones files found in raw.")
        return

    records = []
    for file in json_files:
        with open(file, "r") as f:
            data = json.load(f)

        zones: dict = data.get("zones") or {}
        if not zones:
            continue

        total_seconds = sum(
            (z.get("seconds_in_zone") or 0) for z in zones.values()
        )
        if total_seconds == 0:
            continue

        def _pct(zone_num):
            z = zones.get(zone_num) or zones.get(str(zone_num)) or {}
            secs = z.get("seconds_in_zone") or 0
            return round(100.0 * secs / total_seconds, 1)

        def _boundary(zone_num):
            z = zones.get(zone_num) or zones.get(str(zone_num)) or {}
            return z.get("zone_low_boundary")

        records.append({
            "activity_id": str(data.get("activity_id")),
            "date": data.get("date"),
            "pct_z1": _pct(1),
            "pct_z2": _pct(2),
            "pct_z3": _pct(3),
            "pct_z4": _pct(4),
            "pct_z5": _pct(5),
            "z1_low_bpm": _boundary(1),
            "z2_low_bpm": _boundary(2),
            "z3_low_bpm": _boundary(3),
            "z4_low_bpm": _boundary(4),
            "z5_low_bpm": _boundary(5),
            "total_seconds_in_zones": total_seconds,
        })

    if not records:
        logger.warning("No valid HR zone records to process.")
        return

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    output_path = PROCESSED_DIR / "silver_hr_zones.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} HR zone records to {output_path}")


def process_weight_jsons():
    """Reads weight_history_*.json files and saves a consolidated Silver Parquet."""
    logger.info("Starting weight history processing...")
    files = sorted(glob.glob(str(RAW_DIR / "weight_history_*.json")), reverse=True)
    if not files:
        logger.warning("No weight history files found in raw.")
        return

    # Use only the most recent file — it already covers the full 30-day window
    with open(files[0], "r") as f:
        data = json.load(f)

    summaries = data.get("dailyWeightSummaries") or []
    records = []
    for entry in summaries:
        latest = entry.get("latestWeight") or {}
        weight_g = latest.get("weight")
        records.append({
            "date": entry.get("summaryDate"),
            "weight_kg": round(weight_g / 1000, 2) if weight_g else None,
            "bmi": latest.get("bmi"),
            "body_fat_pct": latest.get("bodyFat"),
            "muscle_mass_kg": round(latest.get("muscleMass") / 1000, 2) if latest.get("muscleMass") else None,
            "source_type": latest.get("sourceType"),
        })

    if not records:
        logger.warning("No weight entries to process.")
        return

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    output_path = PROCESSED_DIR / "silver_weight.parquet"
    df.to_parquet(output_path, index=False)
    logger.info(f"Saved {len(df)} weight records to {output_path}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()

    process_sleep_jsons()
    process_health_telemetry_jsons()
    process_hydration_jsons()
    process_weight_jsons()
    process_fit_files()
    process_hr_zones_jsons()
    process_strength_jsons()
    logger.info("Bronze -> Silver processing complete.")
