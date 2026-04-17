import datetime
import pytest
from pydantic import ValidationError

from src.models.biometrics import AthleteContext, DailyReadiness, RunSummary, ShoeEntry, SubjectiveWellness


# ── SubjectiveWellness ────────────────────────────────────────────────────────

def test_subjective_wellness_valid():
    w = SubjectiveWellness(mood="Good", soreness_level=3, soreness_location="Right calf")
    assert w.mood == "Good"
    assert w.soreness_level == 3


def test_subjective_wellness_all_optional():
    w = SubjectiveWellness()
    assert w.mood is None
    assert w.soreness_level is None


def test_subjective_wellness_soreness_out_of_range():
    with pytest.raises(ValidationError):
        SubjectiveWellness(soreness_level=11)

    with pytest.raises(ValidationError):
        SubjectiveWellness(soreness_level=0)


# ── DailyReadiness ────────────────────────────────────────────────────────────

DB_ROW_READINESS = {
    "date": datetime.date(2026, 3, 21),
    "sleep_time_seconds": 32400,  # 9 hours
    "sleep_score": 89,
    "sleep_score_qualifier": "GOOD",
    "hrv_last_night_avg": 38,
    "hrv_status": "UNBALANCED",
    "body_battery_end": 86,
}


def test_daily_readiness_from_db():
    r = DailyReadiness.from_db(DB_ROW_READINESS)
    assert r.readiness_date == datetime.date(2026, 3, 21)
    assert r.sleep_time_hours == 9.0
    assert r.sleep_score == 89
    assert r.hrv_last_night_avg == 38
    assert r.body_battery_end == 86
    assert r.wellness is None


def test_daily_readiness_from_db_with_wellness():
    wellness = SubjectiveWellness(mood="Tired", soreness_level=4)
    r = DailyReadiness.from_db(DB_ROW_READINESS, wellness=wellness)
    assert r.wellness.mood == "Tired"
    assert r.wellness.soreness_level == 4


def test_daily_readiness_from_db_null_sleep():
    row = {**DB_ROW_READINESS, "sleep_time_seconds": None}
    r = DailyReadiness.from_db(row)
    assert r.sleep_time_hours is None


def test_daily_readiness_llm_payload_excludes_nothing():
    r = DailyReadiness.from_db(DB_ROW_READINESS)
    payload = r.model_dump()
    assert "readiness_date" in payload
    assert "sleep_time_hours" in payload


# ── RunSummary ────────────────────────────────────────────────────────────────

DB_ROW_RUN = {
    "activity_id": "12345",
    "run_date": datetime.datetime(2026, 3, 20, 7, 0, 0),  # Timestamp from DB
    "total_distance_meters": 10000.0,
    "avg_heart_rate": 145.0,
    "max_heart_rate": 160.0,
    "avg_cadence": 82.0,
    "avg_speed_m_s": 2.78,  # ~6:00 min/km
}


def test_run_summary_from_db():
    r = RunSummary.from_db(DB_ROW_RUN)
    assert r.activity_id == "12345"
    assert r.run_date == datetime.date(2026, 3, 20)
    assert r.total_distance_km == 10.0
    assert r.avg_heart_rate == 145.0


def test_run_summary_pace_computed():
    r = RunSummary.from_db(DB_ROW_RUN)
    # 1000 / 2.78 ≈ 359.7s → 5:59
    assert r.avg_pace_per_km == "5:59"


def test_run_summary_pace_zero_speed():
    row = {**DB_ROW_RUN, "avg_speed_m_s": 0}
    r = RunSummary.from_db(row)
    assert r.avg_pace_per_km == "0:00"


def test_run_summary_llm_payload_excludes_speed():
    r = RunSummary.from_db(DB_ROW_RUN)
    payload = r.model_dump()
    assert "avg_speed_m_s" not in payload
    assert "avg_pace_per_km" in payload


def test_run_summary_distance_conversion():
    row = {**DB_ROW_RUN, "total_distance_meters": 21097.5}
    r = RunSummary.from_db(row)
    assert r.total_distance_km == 21.098


# ── AthleteContext ────────────────────────────────────────────────────────────

def test_athlete_context_defaults():
    a = AthleteContext(age=28, weight_kg=70.0, current_shoes="Nike Vaporfly 200km")
    assert a.primary_goal == "Improve Half Marathon time"
    assert a.vo2_max is None
    assert a.target_race_date is None


def test_athlete_context_full():
    a = AthleteContext(
        age=28,
        weight_kg=70.0,
        vo2_max=52.5,
        current_shoes="Nike Vaporfly 200km",
        target_race_date=datetime.date(2026, 10, 15),
    )
    assert a.vo2_max == 52.5
    assert a.target_race_date == datetime.date(2026, 10, 15)


def test_athlete_context_new_fields_optional():
    a = AthleteContext(age=28, weight_kg=70.0, current_shoes="Nike Vaporfly")
    assert a.lthr is None
    assert a.target_pace_min_per_km is None
    assert a.shoes is None


def test_athlete_context_with_lthr_and_pace():
    a = AthleteContext(
        age=28, weight_kg=70.0, current_shoes="Nike Vaporfly",
        lthr=168, target_pace_min_per_km=4.917,
    )
    assert a.lthr == 168
    assert a.target_pace_min_per_km == 4.917


def test_athlete_context_with_shoes():
    shoe = ShoeEntry(name="Vaporfly 3", start_date=datetime.date(2026, 1, 1), max_km=500)
    a = AthleteContext(
        age=28, weight_kg=70.0, current_shoes="Vaporfly 3",
        shoes=[shoe],
    )
    assert len(a.shoes) == 1
    assert a.shoes[0].name == "Vaporfly 3"
    assert a.shoes[0].max_km == 500


# ── ShoeEntry ─────────────────────────────────────────────────────────────────

def test_shoe_entry_defaults():
    s = ShoeEntry(name="Alphafly", start_date=datetime.date(2026, 3, 1))
    assert s.max_km == 600


def test_shoe_entry_custom_max_km():
    s = ShoeEntry(name="Alphafly", start_date=datetime.date(2026, 3, 1), max_km=400)
    assert s.max_km == 400
