from pydantic import BaseModel, Field, model_validator
from typing import List, Optional


class WorkoutInterval(BaseModel):
    """A specific block within a training session."""
    type: str = Field(..., description="Block type: 'Warmup', 'Work', 'Recovery', 'Cooldown'")
    duration: str = Field(..., description="Duration as time or distance (e.g. '15 min', '1 km')")
    target_pace: Optional[str] = Field(None, description="Target pace in min/km (e.g. '4:30 - 4:45')")
    target_hr_zone: Optional[int] = Field(None, ge=1, le=5, description="Target heart rate zone (1 to 5)")
    instructions: str = Field(..., description="Biomechanical cues (e.g. 'Keep cadence above 170 spm')")


class DailyActionPlan(BaseModel):
    """Daily report and training plan emitted by J.A.R.V.I.S."""
    readiness_analysis: str = Field(..., description="Brief analysis of the athlete's status based on sleep, HRV and soreness.")

    # The agent decides whether to train or rest today
    is_rest_day: bool = Field(..., description="True if the athlete should rest or do active recovery today.")

    # Suggested workout (if not a rest day)
    workout_title: Optional[str] = Field(None, description="Workout title (e.g. 'Threshold Intervals 5x1k')")
    workout_blocks: Optional[List[WorkoutInterval]] = Field(None, description="Breakdown of the training blocks")

    # Secondary agents (Nutrition & Hydration)
    hydration_target_liters: float = Field(..., description="Recommended water intake in liters considering weather and training load.")
    nutrition_advice: str = Field(..., description="Macro or meal timing recommendation for pre/post workout.")
    gear_recommendation: Optional[str] = Field(None, description="Shoe recommendation based on the type of workout.")

    @model_validator(mode="after")
    def workout_blocks_required_when_training(self) -> "DailyActionPlan":
        if not self.is_rest_day and not self.workout_blocks:
            raise ValueError("workout_blocks must be provided when is_rest_day is False.")
        return self
