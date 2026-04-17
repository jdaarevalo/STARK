import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field


def format_pace(speed_m_s: float) -> str:
    """Converts speed in m/s to a 'M:SS min/km' string. Returns 'N/A' for zero/None."""
    if not speed_m_s or speed_m_s <= 0:
        return "N/A"
    pace_s = 1000 / speed_m_s
    return f"{int(pace_s // 60)}:{int(pace_s % 60):02d} min/km"


class SubjectiveWellness(BaseModel):
    """
    Subjective inputs reported by the athlete each morning.
    This is what the athlete tells the app — not measured by Garmin.
    In pydantic-ai, this is the output schema the LLM fills during the morning check-in.
    """
    mood: Optional[str] = Field(None, description="Athlete's subjective mood (e.g. 'Good', 'Tired', 'Motivated')")
    soreness_level: Optional[int] = Field(None, ge=1, le=10, description="Muscle soreness from 1 (none) to 10 (severe)")
    soreness_location: Optional[str] = Field(None, description="Location of soreness (e.g. 'Right calf', 'Knee')")


class DailyReadiness(BaseModel):
    """
    Full recovery and physical status for a given day.
    Combines Garmin objective metrics (from DB) with subjective athlete inputs.
    Passed as context to J.A.R.V.I.S. agents to modulate training load.
    """
    readiness_date: datetime.date = Field(..., description="Date of the metric")
    # sleep_time_seconds from DB is converted to hours on construction
    sleep_time_hours: Optional[float] = Field(None, description="Total hours of sleep")
    sleep_score: Optional[int] = Field(None, description="Garmin sleep score (0-100)")
    sleep_score_qualifier: Optional[str] = Field(None, description="Garmin sleep qualifier (e.g. 'GOOD', 'FAIR')")
    hrv_last_night_avg: Optional[int] = Field(None, description="Last night average HRV in milliseconds")
    hrv_status: Optional[str] = Field(None, description="HRV status from Garmin (e.g. 'BALANCED', 'UNBALANCED')")
    body_battery_end: Optional[int] = Field(None, description="Body battery level at end of day (0-100)")
    wellness: Optional[SubjectiveWellness] = Field(None, description="Athlete's subjective morning check-in")

    @classmethod
    def from_db(cls, row: dict, wellness: Optional[SubjectiveWellness] = None) -> "DailyReadiness":
        """Builds a DailyReadiness from a get_daily_readiness() DB row."""
        sleep_seconds = row.get("sleep_time_seconds")
        return cls(
            readiness_date=row["date"],
            sleep_time_hours=round(sleep_seconds / 3600, 2) if sleep_seconds else None,
            sleep_score=row.get("sleep_score"),
            sleep_score_qualifier=row.get("sleep_score_qualifier"),
            hrv_last_night_avg=row.get("hrv_last_night_avg"),
            hrv_status=row.get("hrv_status"),
            body_battery_end=row.get("body_battery_end"),
            wellness=wellness,
        )


class RunSummary(BaseModel):
    """
    Aggregated telemetry from a single run session.
    Passed as context to the Coach Agent.
    avg_speed_m_s is excluded from the LLM payload — avg_pace_per_km is pre-computed instead.
    """
    activity_id: str
    run_date: datetime.date
    total_distance_km: float = Field(..., description="Total distance covered in kilometers")
    avg_heart_rate: Optional[float] = Field(None, description="Average heart rate in beats per minute (bpm)")
    max_heart_rate: Optional[float] = Field(None, description="Maximum heart rate reached (bpm)")
    avg_cadence: Optional[float] = Field(None, description="Average cadence in steps per minute (spm)")
    avg_speed_m_s: Optional[float] = Field(None, exclude=True)  # Excluded from LLM JSON output

    @computed_field
    def avg_pace_per_km(self) -> str:
        """
        Calculates pace in min/km.
        LLMs are poor at math — better to pre-compute this and pass it directly.
        """
        if not self.avg_speed_m_s or self.avg_speed_m_s <= 0:
            return "0:00"
        pace_seconds = 1000 / self.avg_speed_m_s
        minutes = int(pace_seconds // 60)
        seconds = int(pace_seconds % 60)
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def from_db(cls, row: dict) -> "RunSummary":
        """Builds a RunSummary from a get_run_summary() DB row."""
        distance_m = row.get("total_distance_meters") or 0
        return cls(
            activity_id=str(row["activity_id"]),
            run_date=row["run_date"].date() if hasattr(row["run_date"], "date") else row["run_date"],
            total_distance_km=round(distance_m / 1000, 3),
            avg_heart_rate=row.get("avg_heart_rate"),
            max_heart_rate=row.get("max_heart_rate"),
            avg_cadence=row.get("avg_cadence"),
            avg_speed_m_s=row.get("avg_speed_m_s"),
        )


class ShoeEntry(BaseModel):
    """Tracks mileage for a single pair of running shoes."""
    name: str = Field(..., description="Shoe model name (e.g. 'Nike Vaporfly 3')")
    start_date: datetime.date = Field(..., description="Date the shoe was first used")
    max_km: int = Field(default=600, description="Recommended maximum mileage before replacement (km)")


class AthleteContext(BaseModel):
    """Current athlete profile to provide context to the AI."""
    age: int
    weight_kg: float
    vo2_max: Optional[float] = Field(None, description="Current VO2 Max estimated by Garmin")
    current_shoes: str = Field(..., description="Current shoes and approximate mileage on them")
    primary_goal: str = Field(default="Improve Half Marathon time")
    target_race_date: Optional[datetime.date] = Field(None, description="Target Half Marathon race date")
    lthr: Optional[int] = Field(None, description="Lactate Threshold Heart Rate in bpm — used to calculate TSS")
    target_pace_min_per_km: Optional[float] = Field(None, description="Target race pace in decimal min/km (e.g. 4.917 = 4:55)")
    shoes: Optional[list[ShoeEntry]] = Field(None, description="List of active shoe entries for mileage tracking")


# ── Biomechanics report models ─────────────────────────────────────────────────

class MetricFocus(BaseModel):
    """A single biomechanical metric the athlete should focus on improving."""
    metric: str = Field(description="Metric name (e.g. 'Ground Contact Time')")
    current_value: str = Field(description="Current measured average with units (e.g. '287 ms')")
    target_value: str = Field(description="Optimal target range with units (e.g. '240-260 ms')")
    why_it_matters: str = Field(description="One sentence on the biomechanical impact of this metric")
    drill: str = Field(description="One specific drill or cue to improve this metric in the next session")


class RunSnapshot(BaseModel):
    """Per-run biomechanics summary for use inside a BiomechanicsReport."""
    run_date: str = Field(description="Date of the run (YYYY-MM-DD)")
    distance_km: float = Field(description="Total distance in km")
    avg_pace: str = Field(description="Average pace in min/km (e.g. '6:59 min/km')")
    avg_hr: float = Field(description="Average heart rate in bpm")
    avg_cadence_spm: float = Field(description="Average cadence in steps per minute")
    avg_vo_mm: float = Field(description="Average vertical oscillation in mm")
    avg_vr_pct: float = Field(description="Average vertical ratio in %")
    avg_gct_ms: float = Field(description="Average ground contact time in ms")
    cardiac_drift_bpm: Optional[float] = Field(
        None, description="HR increase from first to last third of the run (bpm). Positive = drift."
    )
    cadence_drift_spm: Optional[float] = Field(
        None, description="Cadence change from first to last third (spm). Negative = fatigue shuffle."
    )


class BiomechanicsReport(BaseModel):
    """
    Structured output from the Biomechanics Analysis Agent.
    Can be consumed by the Planner Agent as context for training prescription.
    """
    overall_assessment: str = Field(
        description="2-3 sentence summary of overall running mechanics and efficiency trends across the analyzed runs"
    )
    runs: list[RunSnapshot] = Field(
        description="One snapshot per analyzed run, ordered most recent first"
    )
    key_findings: list[str] = Field(
        description="Top 3-4 specific biomechanical observations backed by numbers (e.g. 'Cadence drops 9 SPM on easy runs')"
    )
    strengths: list[str] = Field(
        description="2-3 things the athlete is doing well biomechanically"
    )
    focus_metrics: list[MetricFocus] = Field(
        description="Top 3 metrics to improve, ordered by impact on performance and injury prevention"
    )
    next_session_cues: list[str] = Field(
        description="3-4 concrete mental cues to apply during the very next run (e.g. 'Think quick feet, aim for 170 SPM')"
    )
