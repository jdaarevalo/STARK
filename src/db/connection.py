import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

import duckdb

# Project root (two levels up: src/db/ -> src/ -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
_ATHLETE_CONFIG_PATH = PROJECT_ROOT / "data" / "athlete_config.json"
_DAILY_INPUT_PATH = PROJECT_ROOT / "data" / "daily_inputs.json"

# All data lives in Parquet files; DuckDB only holds view definitions.
# In-memory avoids the single-writer file lock when multiple processes run simultaneously
# (e.g. Streamlit dashboard + Chainlit chat).
_DB_PATH = ":memory:"

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.db.connection")

# Default absolute HR zone thresholds (bpm) — used when no LTHR is configured.
# Based on common recreational runner ranges; replace with LTHR-based zones when possible.
_DEFAULT_ZONES = (125, 145, 160, 175)  # upper edge of Z1, Z2, Z3, Z4


def _zone_thresholds(lthr: Optional[int]) -> tuple[int, int, int, int]:
    """
    Returns (z1_top, z2_top, z3_top, z4_top) bpm thresholds.
    When LTHR is provided, uses Friel running zones as % of LTHR:
      Z1 < 85%, Z2 85-89%, Z3 90-94%, Z4 95-99%, Z5 >= 100%
    Falls back to absolute defaults when lthr is None or 0.
    """
    if lthr and lthr > 0:
        return (
            round(lthr * 0.85),
            round(lthr * 0.90),
            round(lthr * 0.95),
            round(lthr * 1.00),
        )
    return _DEFAULT_ZONES


def _hr_zones_sql(lthr: Optional[int] = None) -> str:
    """Generates the HR zone percentage SQL fragment for a given LTHR."""
    z1, z2, z3, z4 = _zone_thresholds(lthr)
    return f"""\
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate < {z1})                    / COUNT(*), 1) AS pct_z1,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN {z1} AND {z2 - 1}) / COUNT(*), 1) AS pct_z2,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN {z2} AND {z3 - 1}) / COUNT(*), 1) AS pct_z3,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN {z3} AND {z4 - 1}) / COUNT(*), 1) AS pct_z4,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate >= {z4})                   / COUNT(*), 1) AS pct_z5,"""


class StarkDatabase:
    """
    Singleton managing the DuckDB connection (The Arc Reactor).
    Handles the Gold layer: aggregations and semantic views over the Silver layer (Parquet).
    """
    _instance = None

    def __new__(cls, db_path: str = _DB_PATH):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize(db_path)
        return cls._instance

    def _initialize(self, db_path: str) -> None:
        logger.info(f"Starting Arc Reactor boot sequence ({db_path})...")
        self.conn = duckdb.connect(db_path)
        self._setup_views()

    def _setup_views(self) -> None:
        """
        Creates logical views over the Parquet files.
        DuckDB reads directly from /processed on the fly.
        """
        logger.info("Setting up telemetry interface (views over Parquet)...")

        self.conn.execute(f"""
            CREATE OR REPLACE VIEW gold_sleep AS
            SELECT * FROM read_parquet('{PROCESSED_DIR}/silver_sleep_data.parquet')
        """)

        self.conn.execute(f"""
            CREATE OR REPLACE VIEW gold_health AS
            SELECT * FROM read_parquet('{PROCESSED_DIR}/silver_health_telemetry.parquet')
        """)

        # union_by_name=true: if a run was recorded without a chest strap and a column
        # is missing, DuckDB fills it with NULLs instead of failing.
        self.conn.execute(f"""
            CREATE OR REPLACE VIEW gold_runs AS
            SELECT
                filename AS source_file,
                *
            FROM read_parquet('{PROCESSED_DIR}/silver_run_*.parquet', union_by_name=true, filename=true)
        """)

        hydration_parquet = PROCESSED_DIR / "silver_hydration.parquet"
        if hydration_parquet.exists():
            self.conn.execute(f"""
                CREATE OR REPLACE VIEW gold_hydration AS
                SELECT * FROM read_parquet('{hydration_parquet}')
            """)

        logger.info("Gold layer views ready.")

    def get_run_summary(self, activity_id: str) -> Optional[Dict[str, Any]]:
        """Returns aggregated metrics for a single run activity."""
        query = """
            SELECT
                ? AS activity_id,
                MIN(timestamp)      AS run_date,
                MAX(distance)       AS total_distance_meters,
                AVG(heart_rate)     AS avg_heart_rate,
                MAX(heart_rate)     AS max_heart_rate,
                AVG(cadence)        AS avg_cadence,
                AVG(enhanced_speed) AS avg_speed_m_s
            FROM gold_runs
            WHERE source_file LIKE ?
        """
        try:
            result = self.conn.execute(query, [activity_id, f"%{activity_id}%"]).df()
            if result.empty or result["run_date"].isnull().all():
                return None
            return result.to_dict(orient="records")[0]
        except Exception as e:
            logger.error(f"Failed to retrieve run telemetry for {activity_id}: {e}")
            return None

    def get_recent_runs(self, limit: int = 4, lthr: Optional[int] = None) -> list:
        """Returns detailed telemetry for the most recent runs, ordered by date descending."""
        query = f"""
            SELECT
                source_file,
                MIN(timestamp)                                  AS run_date,
                epoch_ms(MAX(timestamp) - MIN(timestamp)) / 60000.0 AS duration_minutes,
                MAX(distance)                                   AS total_distance_meters,
                AVG(power)                                      AS avg_power_w,
                MAX(power)                                      AS max_power_w,
                AVG(heart_rate)                                 AS avg_heart_rate,
                MAX(heart_rate)                                 AS max_heart_rate,
                {_hr_zones_sql(lthr)}
                AVG(enhanced_speed)                             AS avg_speed_m_s,
                AVG(cadence) * 2                                AS avg_cadence_spm,
                AVG(step_length)                                AS avg_step_length_mm,
                AVG(vertical_oscillation)                       AS avg_vertical_oscillation_mm,
                AVG(vertical_ratio)                             AS avg_vertical_ratio_pct,
                AVG(stance_time)                                AS avg_stance_time_ms,
                AVG(temperature)                                AS avg_temperature_c,
                AVG(enhanced_altitude)                          AS avg_altitude_m
            FROM gold_runs
            GROUP BY source_file
            ORDER BY run_date DESC
            LIMIT ?
        """
        try:
            result = self.conn.execute(query, [limit]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve recent runs: {e}")
            return []

    def get_run_biomechanics(self, limit: int = 3, lthr: Optional[int] = None) -> list:
        """
        Returns detailed biomechanics telemetry for the most recent runs.
        Includes drift metrics (first vs last third) to detect fatigue degradation.
        Only counts records where the athlete is actually running (cadence > 0, speed > 0.5 m/s).
        """
        query = f"""
            WITH base AS (
                SELECT
                    source_file,
                    timestamp,
                    distance,
                    heart_rate,
                    cadence,
                    enhanced_speed,
                    vertical_oscillation,
                    vertical_ratio,
                    stance_time,
                    step_length,
                    power,
                    enhanced_altitude,
                    ROW_NUMBER() OVER (PARTITION BY source_file ORDER BY timestamp) AS rn,
                    COUNT(*)    OVER (PARTITION BY source_file)                     AS total_rows
                FROM gold_runs
                WHERE cadence > 0 AND enhanced_speed > 0.5
            )
            SELECT
                source_file,
                MIN(timestamp)                                          AS run_date,
                MAX(distance)                                           AS total_distance_m,
                epoch_ms(MAX(timestamp) - MIN(timestamp)) / 60000.0    AS duration_min,

                -- Pace / Speed
                ROUND(AVG(enhanced_speed), 3)                           AS avg_speed_m_s,

                -- Cadence (raw value is half-cadence → multiply by 2 for SPM)
                ROUND(AVG(cadence) * 2, 1)                              AS avg_cadence_spm,
                ROUND(STDDEV(cadence) * 2, 1)                           AS std_cadence_spm,
                ROUND(MIN(cadence) * 2, 0)                              AS min_cadence_spm,
                ROUND(MAX(cadence) * 2, 0)                              AS max_cadence_spm,

                -- Vertical Oscillation (mm)
                ROUND(AVG(vertical_oscillation), 1)                     AS avg_vo_mm,
                ROUND(STDDEV(vertical_oscillation), 1)                  AS std_vo_mm,

                -- Vertical Ratio (%)
                ROUND(AVG(vertical_ratio), 2)                           AS avg_vr_pct,

                -- Ground Contact Time / Stance Time (ms)
                ROUND(AVG(stance_time), 0)                              AS avg_gct_ms,
                ROUND(STDDEV(stance_time), 0)                           AS std_gct_ms,
                ROUND(MIN(stance_time), 0)                              AS min_gct_ms,
                ROUND(MAX(stance_time), 0)                              AS max_gct_ms,

                -- Step Length (mm)
                ROUND(AVG(step_length), 0)                              AS avg_step_length_mm,
                ROUND(STDDEV(step_length), 0)                           AS std_step_length_mm,

                -- Power (W)
                ROUND(AVG(power), 1)                                    AS avg_power_w,
                ROUND(MAX(power), 0)                                    AS max_power_w,
                ROUND(STDDEV(power), 1)                                 AS std_power_w,

                -- Heart Rate
                ROUND(AVG(heart_rate), 1)                               AS avg_hr,
                MAX(heart_rate)                                         AS max_hr,
                {_hr_zones_sql(lthr)}

                -- Fatigue drift: first third vs last third of the run
                ROUND(AVG(CASE WHEN rn <= total_rows / 3 THEN enhanced_speed END), 3)   AS speed_first_third_m_s,
                ROUND(AVG(CASE WHEN rn >  2 * total_rows / 3 THEN enhanced_speed END), 3) AS speed_last_third_m_s,
                ROUND(AVG(CASE WHEN rn <= total_rows / 3 THEN heart_rate END), 1)       AS hr_first_third,
                ROUND(AVG(CASE WHEN rn >  2 * total_rows / 3 THEN heart_rate END), 1)   AS hr_last_third,
                ROUND(AVG(CASE WHEN rn <= total_rows / 3 THEN cadence END) * 2, 1)      AS cadence_first_third_spm,
                ROUND(AVG(CASE WHEN rn >  2 * total_rows / 3 THEN cadence END) * 2, 1)  AS cadence_last_third_spm,
                ROUND(AVG(CASE WHEN rn <= total_rows / 3 THEN stance_time END), 0)      AS gct_first_third_ms,
                ROUND(AVG(CASE WHEN rn >  2 * total_rows / 3 THEN stance_time END), 0)  AS gct_last_third_ms,

                -- Elevation
                ROUND(MAX(enhanced_altitude) - MIN(enhanced_altitude), 1)               AS elevation_range_m
            FROM base
            GROUP BY source_file
            ORDER BY run_date DESC
            LIMIT ?
        """
        try:
            result = self.conn.execute(query, [limit]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve biomechanics data: {e}")
            return []

    def get_health_trend(self, days: int = 30) -> list:
        """Returns daily health metrics for the last N days, ordered chronologically."""
        since = (date.today() - timedelta(days=days)).isoformat()
        query = """
            SELECT
                CAST(h.date AS DATE)                            AS date,
                h.resting_heart_rate,
                h.hrv_last_night_avg,
                h.hrv_status,
                h.body_battery_end,
                h.avg_stress_level,
                h.vo2_max,
                s.sleep_score,
                s.sleep_score_qualifier,
                ROUND(s.sleep_time_seconds / 3600.0, 2)        AS sleep_hours
            FROM gold_health h
            LEFT JOIN gold_sleep s
                   ON CAST(h.date AS DATE) = CAST(s.date AS DATE)
            WHERE CAST(h.date AS DATE) >= CAST(? AS DATE)
            ORDER BY date ASC
        """
        try:
            result = self.conn.execute(query, [since]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve health trend: {e}")
            return []

    def get_hydration_trend(self, days: int = 30) -> list:
        """
        Returns daily hydration metrics for the last N days.
        Each row: date, intake_ml, goal_ml, sweat_loss_ml, pct_of_goal.
        """
        since = (date.today() - timedelta(days=days)).isoformat()
        query = """
            SELECT
                CAST(date AS DATE)                                          AS date,
                ROUND(intake_ml, 0)                                         AS intake_ml,
                ROUND(goal_ml, 0)                                           AS goal_ml,
                sweat_loss_ml,
                ROUND(100.0 * intake_ml / NULLIF(goal_ml, 0), 1)           AS pct_of_goal
            FROM gold_hydration
            WHERE CAST(date AS DATE) >= CAST(? AS DATE)
              AND intake_ml IS NOT NULL
            ORDER BY date ASC
        """
        try:
            result = self.conn.execute(query, [since]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve hydration trend: {e}")
            return []

    def get_daily_readiness(self, target_date: str) -> Optional[Dict[str, Any]]:
        """Returns sleep and recovery metrics for a specific date."""
        query = """
            SELECT
                CAST(s.date AS VARCHAR)  AS date,
                s.sleep_time_seconds,
                s.deep_sleep_seconds,
                s.rem_sleep_seconds,
                s.sleep_score,
                s.sleep_score_qualifier,
                s.avg_heart_rate,
                s.avg_stress,
                h.hrv_last_night_avg,
                h.hrv_status,
                h.body_battery_end,
                h.vo2_max
            FROM gold_sleep s
            LEFT JOIN gold_health h ON CAST(s.date AS DATE) = CAST(h.date AS DATE)
            WHERE CAST(s.date AS DATE) = CAST(? AS DATE)
        """
        try:
            result = self.conn.execute(query, [target_date]).df()
            if result.empty:
                return None
            return result.to_dict(orient="records")[0]
        except Exception as e:
            logger.error(f"Failed to retrieve readiness for {target_date}: {e}")
            return None

    def get_weekly_intensity(self, weeks: int = 2, lthr: Optional[int] = None) -> list:
        """
        Returns minutes spent per HR zone per week for the last N weeks.
        Zone thresholds use Friel % of LTHR when provided; fall back to absolute bpm defaults.
        Each row: week_start, z1_min, z2_min, z3_min, z4_min, z5_min, total_min.
        """
        z1, z2, z3, z4 = _zone_thresholds(lthr)
        query = f"""
            WITH runs AS (
                SELECT
                    source_file,
                    timestamp,
                    heart_rate,
                    epoch_ms(MAX(timestamp) OVER (PARTITION BY source_file)
                           - MIN(timestamp) OVER (PARTITION BY source_file)) / 60000.0 AS run_duration_min,
                    COUNT(*) OVER (PARTITION BY source_file) AS record_count
                FROM gold_runs
                WHERE heart_rate IS NOT NULL
            ),
            weekly AS (
                SELECT
                    DATE_TRUNC('week', timestamp)::DATE                                         AS week_start,
                    SUM(run_duration_min / record_count)
                        FILTER (WHERE heart_rate < {z1})                                        AS z1_min,
                    SUM(run_duration_min / record_count)
                        FILTER (WHERE heart_rate BETWEEN {z1} AND {z2 - 1})                     AS z2_min,
                    SUM(run_duration_min / record_count)
                        FILTER (WHERE heart_rate BETWEEN {z2} AND {z3 - 1})                     AS z3_min,
                    SUM(run_duration_min / record_count)
                        FILTER (WHERE heart_rate BETWEEN {z3} AND {z4 - 1})                     AS z4_min,
                    SUM(run_duration_min / record_count)
                        FILTER (WHERE heart_rate >= {z4})                                       AS z5_min
                FROM runs
                GROUP BY DATE_TRUNC('week', timestamp)::DATE
                ORDER BY week_start DESC
                LIMIT ?
            )
            SELECT
                week_start,
                COALESCE(z1_min, 0) AS z1_min,
                COALESCE(z2_min, 0) AS z2_min,
                COALESCE(z3_min, 0) AS z3_min,
                COALESCE(z4_min, 0) AS z4_min,
                COALESCE(z5_min, 0) AS z5_min,
                COALESCE(z1_min, 0) + COALESCE(z2_min, 0) + COALESCE(z3_min, 0)
                    + COALESCE(z4_min, 0) + COALESCE(z5_min, 0)                                AS total_min
            FROM weekly
            ORDER BY week_start ASC
        """
        try:
            result = self.conn.execute(query, [weeks]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve weekly intensity: {e}")
            return []

    def get_efficiency_trend(self, weeks: int = 16, lthr: Optional[int] = None) -> list:
        """
        Returns weekly aerobic efficiency for easy runs (Z1+Z2 >= 70% of HR records).
        Zone boundary uses LTHR-based threshold when provided (Z3 lower edge = 90% LTHR),
        otherwise falls back to 145 bpm absolute.
        Each row: week_start, avg_pace_sec_km, avg_hr, run_count, total_km.
        """
        _, z2, _, _ = _zone_thresholds(lthr)  # Z3 starts at z2 threshold
        query = f"""
            WITH run_zones AS (
                SELECT
                    source_file,
                    MIN(timestamp)                                          AS run_date,
                    AVG(enhanced_speed)                                     AS avg_speed_m_s,
                    AVG(heart_rate)                                         AS avg_hr,
                    MAX(distance)                                           AS total_dist_m,
                    -- fraction of records in Z1+Z2 (below Z3 threshold)
                    COUNT(*) FILTER (WHERE heart_rate < {z2}) * 1.0
                        / NULLIF(COUNT(*) FILTER (WHERE heart_rate IS NOT NULL), 0) AS easy_fraction
                FROM gold_runs
                WHERE enhanced_speed > 0.5 AND heart_rate IS NOT NULL
                GROUP BY source_file
            ),
            easy_runs AS (
                SELECT
                    DATE_TRUNC('week', run_date)::DATE  AS week_start,
                    -- pace in seconds per km
                    1000.0 / NULLIF(AVG(avg_speed_m_s), 0) AS avg_pace_sec_km,
                    AVG(avg_hr)                             AS avg_hr,
                    COUNT(*)                                AS run_count,
                    SUM(total_dist_m) / 1000.0              AS total_km
                FROM run_zones
                WHERE easy_fraction >= 0.70
                GROUP BY DATE_TRUNC('week', run_date)::DATE
                ORDER BY week_start DESC
                LIMIT ?
            )
            SELECT * FROM easy_runs ORDER BY week_start ASC
        """
        try:
            result = self.conn.execute(query, [weeks]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve efficiency trend: {e}")
            return []

    def get_training_load_history(self, days: int = 42) -> list:
        """
        Returns daily Training Stress Score (TSS) for the last N days.
        Uses rTSS (Running Power-based) when power data is available; falls back to
        hrTSS (Heart Rate-based, requires LTHR) when power is absent.
        Each row: run_date, tss_power, tss_hr_numerator (divide by LTHR^2 * 3600 * 100 outside).
        The dashboard computes ATL/CTL/TSB from this in pandas.
        """
        since = (date.today() - timedelta(days=days)).isoformat()
        query = """
            WITH run_stats AS (
                SELECT
                    CAST(MIN(timestamp) AS DATE)                            AS run_date,
                    epoch_ms(MAX(timestamp) - MIN(timestamp)) / 1000.0      AS duration_sec,
                    AVG(heart_rate)                                         AS avg_hr,
                    AVG(power)                                              AS avg_power_w,
                    -- Functional Threshold Power proxy: use 95th percentile of power as FTP
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY power)     AS ftp_proxy_w
                FROM gold_runs
                WHERE CAST(timestamp AS DATE) >= CAST(? AS DATE)
                  AND heart_rate IS NOT NULL
                GROUP BY source_file
            )
            SELECT
                run_date,
                duration_sec,
                avg_hr,
                -- rTSS components (power-based) — valid when avg_power_w IS NOT NULL
                avg_power_w,
                ftp_proxy_w,
                -- hrTSS numerator = duration_sec * avg_hr * avg_hr
                -- Full formula: hrTSS = (duration_sec * avg_hr^2) / (LTHR^2 * 3600) * 100
                -- LTHR is provided by the dashboard from athlete_config.json
                ROUND(duration_sec * avg_hr * avg_hr, 0) AS hr_tss_numerator
            FROM run_stats
            ORDER BY run_date ASC
        """
        try:
            result = self.conn.execute(query, [since]).df()
            if result.empty:
                return []
            return result.to_dict(orient="records")
        except Exception as e:
            logger.error(f"Failed to retrieve training load history: {e}")
            return []

    def get_km_since(self, start_date: str) -> float:
        """Returns total km run from gold_runs on or after start_date."""
        query = """
            SELECT COALESCE(SUM(max_dist) / 1000.0, 0.0) AS total_km
            FROM (
                SELECT source_file, MAX(distance) AS max_dist
                FROM gold_runs
                WHERE CAST(timestamp AS DATE) >= CAST(? AS DATE)
                GROUP BY source_file
            )
        """
        try:
            result = self.conn.execute(query, [start_date]).fetchone()
            return round(result[0], 1) if result else 0.0
        except Exception as e:
            logger.error(f"Failed to calculate km since {start_date}: {e}")
            return 0.0

    def close(self) -> None:
        """Shuts down the Arc Reactor safely."""
        self.conn.close()
        logger.info("Arc Reactor disconnected.")


# ── Athlete config + daily input persistence (JSON, not DuckDB) ───────────────
# These are user-supplied values (shoe list, LTHR, target pace, daily soreness).
# DuckDB is in-memory and can't persist across restarts, so we use plain JSON files.

def load_athlete_config() -> dict:
    """Loads athlete config from data/athlete_config.json. Returns {} if missing."""
    if _ATHLETE_CONFIG_PATH.exists():
        try:
            return json.loads(_ATHLETE_CONFIG_PATH.read_text())
        except Exception as e:
            logger.error(f"Failed to load athlete config: {e}")
    return {}


def save_athlete_config(config: dict) -> None:
    """Persists athlete config to data/athlete_config.json."""
    _ATHLETE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ATHLETE_CONFIG_PATH.write_text(json.dumps(config, indent=2, default=str))
    logger.info("Athlete config saved.")


def load_daily_inputs() -> dict:
    """Loads daily athlete inputs (soreness etc.) from data/daily_inputs.json."""
    if _DAILY_INPUT_PATH.exists():
        try:
            return json.loads(_DAILY_INPUT_PATH.read_text())
        except Exception as e:
            logger.error(f"Failed to load daily inputs: {e}")
    return {}


def save_daily_input(entry_date: str, soreness: int) -> None:
    """Saves soreness for a given date. Overwrites previous entry for the same date."""
    _DAILY_INPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    inputs = load_daily_inputs()
    inputs[entry_date] = {"soreness": soreness}
    _DAILY_INPUT_PATH.write_text(json.dumps(inputs, indent=2))
    logger.info(f"Daily input saved for {entry_date}: soreness={soreness}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.config.logging_config import setup_logging
    setup_logging()

    db = StarkDatabase()

    print("\n--- J.A.R.V.I.S. System Test ---")
    readiness = db.get_daily_readiness("2026-03-21")
    print(f"Readiness 2026-03-21: {readiness}")

    run = db.get_run_summary("22240207997")
    print(f"Run summary: {run}")

    db.close()
