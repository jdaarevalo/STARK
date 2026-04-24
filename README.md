# ⚡ S.T.A.R.K.
**Smart Training & Athletic Readiness Kernel**

> *"J.A.R.V.I.S., bring up my biometric telemetry and plot a course for a sub-1:59 Half Marathon."*

S.T.A.R.K. is a local data engineering pipeline and single-agent AI system for athletic performance optimization. It ingests raw biometric telemetry from Garmin wearables, processes it through a Medallion Architecture (Bronze → Silver → Gold), and exposes it to a unified LLM agent that acts as a panel of experts — physiologist, biomechanics coach, and sports nutritionist — in a single fast response.

---

## System Architecture

```
Garmin API / .FIT files
        │
        ▼
  Bronze (data/raw/)          Raw JSONs + .FIT ZIPs
        │  garmin.py
        ▼
  Silver (data/processed/)    Parquet files
        │  transformations.py
        ▼
  Gold (DuckDB in-memory)     Views + aggregation queries
        │  connection.py
        ▼
  J.A.R.V.I.S. Agent          1 LLM call, 4 tools
        │  jarvis_agent.py
        ▼
  FastAPI SSE chat (port 7934) + Streamlit dashboard (port 8501)
```

### Agent design — hybrid context injection + on-demand tools

J.A.R.V.I.S. injects a lightweight snapshot (~400 tokens) on the first message, then fetches deeper data via tools only when the question requires it.

```
First message only:
  inject → recovery snapshot + athlete profile + weight + today/tomorrow workouts + race predictions

Every message:
    │
    ▼
jarvis_agent  (1 LLM call)
    ├── [context] recovery, profile, weight, garmin_coach_today_tomorrow, race_predictions
    ├── get_training_load()        → ATL/CTL/TSB, ACR, injury risk (on demand)
    ├── get_recent_runs(n)         → last N runs with biomechanics (on demand)
    ├── get_health_trend(days)     → multi-day HRV/sleep/RHR trend (on demand)
    ├── get_upcoming_workouts()    → full Garmin Coach week schedule (on demand)
    └── query_athlete_data(sql)    → ad-hoc SELECT against gold views (on demand)
    │
    ▼
Synthesized response (plain text, J.A.R.V.I.S. tone)
```

Specialist agents (`planner_agent`, `biomechanics_agent`) remain available on their own ports for direct access and development.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Package manager | `uv` |
| Data extraction | `garminconnect`, `fitparse`, `pyarrow` |
| Local data warehouse | `DuckDB` (in-memory, OLAP) |
| Data validation | `Pydantic` |
| Agent framework | `pydantic-ai` |
| LLM | `Gemini` via `pydantic-ai` (`GoogleModel`) |
| Dashboard | `Streamlit` + `Plotly` |
| Chat API | `FastAPI` (SSE streaming) |
| Auth automation | `Playwright` (Chromium) |
| Testing | `Pytest` |

---

## Repository Structure

```
STARK/
├── data/                               # Local data lakehouse (git-ignored)
│   ├── raw/                            # Bronze: raw Garmin JSONs & .FIT ZIPs
│   ├── processed/                      # Silver: cleansed Parquet files
│   ├── athlete_config.json             # Athlete profile (LTHR, pace, shoes)
│   └── daily_inputs.json               # Daily soreness log, keyed by date
├── docs/
│   ├── agents_ideas.md                 # Design notes — panel of experts concept
│   ├── dashboard_feedback.md           # Dashboard design feedback
│   └── orchestrator_plan.md            # Agent architecture history & decisions
├── scripts/
│   └── garmin_auth.py                  # One-time Playwright browser auth
├── src/
│   ├── config/
│   │   ├── agents.py                   # GoogleModel factory (get_google_model)
│   │   └── logging_config.py           # Centralized logging — setup_logging()
│   ├── extractors/
│   │   └── garmin.py                   # Garmin API: sleep, health telemetry, .FIT runs
│   ├── db/
│   │   ├── transformations.py          # Bronze → Silver: JSON/FIT → Parquet
│   │   └── connection.py               # Silver → Gold: DuckDB views, StarkDatabase,
│   │                                   # aggregation tools, athlete config persistence
│   ├── models/
│   │   ├── biometrics.py               # DailyReadiness, RunSummary, BiomechanicsReport
│   │   └── workouts.py                 # DailyActionPlan, WorkoutInterval
│   ├── agents/
│   │   ├── jarvis_agent.py             # Unified J.A.R.V.I.S. agent — primary entry point
│   │   ├── orchestrator.py             # Legacy orchestrator (routing chain) — kept for reference
│   │   ├── planner_agent.py            # Training planner specialist — port 7932
│   │   └── biomechanics_agent.py       # Biomechanics specialist — port 7933
│   └── chat/
│       └── fastapi_app.py              # FastAPI SSE chat UI — port 7934
├── tests/
│   ├── test_extractors.py
│   ├── test_transformations.py
│   ├── test_connection.py
│   ├── test_connection_queries.py
│   └── test_biometrics.py
├── main.py                             # Streamlit dashboard — port 8501
└── pyproject.toml
```

---

## Setup

**Prerequisites:** Python 3.11+, [`uv`](https://github.com/astral-sh/uv)

```bash
git clone <repo-url>
cd STARK
uv sync

# Install Playwright's Chromium browser (required for Garmin auth)
uv run playwright install chromium
```

Create a `.env` file in the project root:

```
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword
GOOGLE_API_KEY=your_google_ai_key
```

---

## Running

### First-time Garmin authentication

Garmin's SSO blocks headless login. Run this once to authenticate via a real browser and save session tokens to `~/.garminconnect/`:

```bash
uv run python scripts/garmin_auth.py
```

### Dashboard (Streamlit)

```bash
streamlit run main.py
# Opens at http://localhost:8501
```

The dashboard includes a "Sync Data" button that runs the full extraction + transformation pipeline in-browser.

### J.A.R.V.I.S. chat (FastAPI)

```bash
uv run python -m uvicorn src.chat.fastapi_app:app --port 7934 --reload
# Opens at http://localhost:7934
```

The chat UI is also embedded in the J.A.R.V.I.S. tab of the Streamlit dashboard.

### Specialist agents (direct access / development)

```bash
uv run python src/agents/planner_agent.py        # port 7932
uv run python src/agents/biomechanics_agent.py   # port 7933
```

### Run the pipeline manually (without Streamlit)

```bash
# 1. Extract raw data from Garmin → Bronze layer
uv run python src/extractors/garmin.py

# 2. Transform raw data → Silver layer (Parquet)
uv run python src/db/transformations.py
```

---

## Data Layers

### Bronze (`data/raw/`)

| File pattern | Description |
|---|---|
| `sleep_data_YYYY-MM-DD.json` | Raw sleep session from Garmin API |
| `health_telemetry_YYYY-MM-DD.json` | HRV, body battery, stress, VO2 Max, steps |
| `run_YYYY-MM-DD_<id>.zip` | Original `.FIT` binary (second-by-second telemetry) |
| `hydration_YYYY-MM-DD.json` | Daily water intake from Garmin Connect |
| `weight_history_YYYY-MM-DD.json` | 30-day weigh-in history |
| `training_plan_YYYY-MM-DD.json` | Active Garmin Coach adaptive plan (full task list) |
| `race_predictions_YYYY-MM-DD.json` | Garmin's 5K / 10K / HM / marathon time estimates |

### Silver (`data/processed/`)

| File | Key columns |
|---|---|
| `silver_sleep_data.parquet` | `date`, `sleep_score`, `deep/rem/light/awake_seconds`, `avg_heart_rate`, `avg_stress` |
| `silver_health_telemetry.parquet` | `date`, `resting_heart_rate`, `hrv_last_night_avg`, `hrv_status`, `body_battery_end`, `vo2_max` |
| `silver_run_<id>.parquet` | `timestamp`, `distance`, `heart_rate`, `cadence`, `power`, `enhanced_speed`, `vertical_oscillation`, `vertical_ratio`, `stance_time`, `step_length` |
| `silver_hydration.parquet` | `date`, `intake_ml`, `goal_ml`, `sweat_loss_ml` |
| `silver_weight.parquet` | `date`, `weight_kg`, `bmi`, `body_fat_pct`, `muscle_mass_kg` |

### Gold (DuckDB in-memory)

| View | Source |
|---|---|
| `gold_sleep` | `silver_sleep_data.parquet` |
| `gold_health` | `silver_health_telemetry.parquet` |
| `gold_runs` | All `silver_run_*.parquet` (union by name) |
| `gold_hydration` | `silver_hydration.parquet` |
| `gold_weight` | `silver_weight.parquet` |

The database is **in-memory** (`:memory:`) to allow Streamlit and FastAPI to run simultaneously without file-lock conflicts.

---

## Dashboard Sections

| Section | Data source | What it shows |
|---|---|---|
| Sidebar — Daily Readiness | `get_health_trend(7)` | HRV, RHR, Body Battery, Sleep vs 7d avg + soreness slider |
| Race Countdown | `athlete_config.json` + `load_race_predictions()` | Days to race, target pace, shoe mileage %, Garmin race time predictions with HM delta vs target |
| Load Balance | `get_training_load_history(42)` | ATL / CTL / TSB — requires LTHR |
| Intensity Distribution | `get_weekly_intensity(2)` | % time per HR zone vs 80/20 target |
| Run Efficiency | `get_efficiency_trend(16)` | Pace vs HR on easy runs (avg HR < Z3 threshold) |
| Historical Trends | `get_health_trend(30)` | HRV/Battery, RHR, Sleep Score, Hydration, Weight (30-day with trend line) |

---

## Port Registry

| Service | Port | Notes |
|---|---|---|
| Streamlit dashboard | 8501 | `streamlit run main.py` |
| FastAPI chat (J.A.R.V.I.S.) | 7934 | Unified agent, embedded in dashboard |
| Planner agent | 7932 | Direct access / development |
| Biomechanics agent | 7933 | Direct access / development |
| Next specialist | 7935 | — |

---

## Tests

```bash
uv run pytest tests/ -v
```

Covers: Garmin API extraction (mocked), Bronze → Silver transformations, DuckDB connection layer, aggregation queries (intensity, efficiency, load, km_since), and Pydantic biometric models.
