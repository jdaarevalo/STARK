import logging
from pathlib import Path
from typing import Optional, Dict, Any

import duckdb

# Project root (two levels up: src/db/ -> src/ -> root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "duckdb" / "runner_data.db"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.db.connection")


class StarkDatabase:
    """
    Singleton managing the DuckDB connection (The Arc Reactor).
    Handles the Gold layer: aggregations and semantic views over the Silver layer (Parquet).
    """
    _instance = None

    def __new__(cls, db_path: Path = DB_PATH):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize(db_path)
        return cls._instance

    def _initialize(self, db_path: Path) -> None:
        logger.info(f"Starting Arc Reactor boot sequence at {db_path}...")
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Persistent connection to local DuckDB file
        self.conn = duckdb.connect(str(db_path))
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

    def get_recent_runs(self, limit: int = 4) -> list:
        """Returns detailed telemetry for the most recent runs, ordered by date descending."""
        query = """
            SELECT
                source_file,
                MIN(timestamp)                                  AS run_date,
                epoch_ms(MAX(timestamp) - MIN(timestamp)) / 60000.0 AS duration_minutes,
                MAX(distance)                                   AS total_distance_meters,
                MAX(accumulated_power)                          AS total_accumulated_power_w,
                AVG(power)                                      AS avg_power_w,
                MAX(power)                                      AS max_power_w,
                AVG(heart_rate)                                 AS avg_heart_rate,
                MAX(heart_rate)                                 AS max_heart_rate,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate < 125)              / COUNT(*), 1) AS pct_z1,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN 125 AND 144) / COUNT(*), 1) AS pct_z2,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN 145 AND 159) / COUNT(*), 1) AS pct_z3,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate BETWEEN 160 AND 174) / COUNT(*), 1) AS pct_z4,
                ROUND(100.0 * COUNT(*) FILTER (WHERE heart_rate >= 175)              / COUNT(*), 1) AS pct_z5,
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

    def close(self) -> None:
        """Shuts down the Arc Reactor safely."""
        self.conn.close()
        logger.info("Arc Reactor disconnected.")


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
