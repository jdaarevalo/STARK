# ⚡️ S.T.A.R.K.
**Smart Training & Athletic Readiness Kernel**

> *"J.A.R.V.I.S., bring up my biometric telemetry and plot a course for a sub-1:45 Half Marathon."*

S.T.A.R.K. is a localized, AI-driven data engineering pipeline and multi-agent orchestration system designed to optimize athletic performance. It ingests raw biometric telemetry (Garmin wearables, sleep data, subjective mood, and pain inputs), processes it through a local OLAP engine, and utilizes LLM-based agents with strict data contracts to generate dynamic, highly personalized training blocks.

## 🏗 System Architecture

This project implements a localized Medallion Architecture (Bronze -> Silver -> Gold) combined with an Agentic Workflow:

1. **Ingestion (The Sensors):** Automated extraction of `.FIT` files, daily sleep scores, and HRV data via the unofficial Garmin API (`garminconnect`).
2. **Storage & Compute (The Arc Reactor):** In-memory and local disk processing using **DuckDB**. Raw JSONs and binary `.FIT` files are transformed into queried views and Parquet files for fast analytical read loads.
3. **Data Contracts (The Suit):** **Pydantic** models ensure strict type-checking and validation. LLMs are forced to output structured JSON matching these models, preventing hallucinations in training plans.
4. **Agent Orchestration (J.A.R.V.I.S.):** A master LLM agent that routes context to specialized sub-agents:
    * 🏃🏽‍♂️ **Coach Agent:** Analyzes pacing, VO2 Max, and mileage to prescribe specific workouts.
    * 🩺 **Physio Agent:** Reviews sleep data, HRV, and subjective pain inputs to adjust load and prevent injury.
    * 💧 **Nutrition Agent:** Recommends hydration and fueling strategies based on upcoming long runs.

## 🛠 Tech Stack

* **Language:** Python 3.11+
* **Package Manager:** `uv`
* **Data Extraction:** `garminconnect`, `fitparse`, `pyarrow`
* **Local Data Warehouse:** `DuckDB` (OLAP)
* **Data Validation & Agent Tooling:** `Pydantic`, `pydantic-ai`
* **Agent UI:** `Chainlit`
* **LLM Backend:** `Gemini` (`gemini-3.1-pro-preview` via `pydantic-ai`)
* **Visualization:** `Pandas`, `Plotly`
* **Environment Management:** `python-dotenv`
* **Testing:** `Pytest`

## 📂 Repository Structure

```text
S.T.A.R.K./
├── data/                           # Local Data Lakehouse (git-ignored)
│   ├── raw/                        # Bronze: Raw Garmin JSONs & .FIT ZIPs
│   ├── processed/                  # Silver: Cleansed Parquet files
│   └── duckdb/                     # Gold: runner_data.db (DuckDB)
├── scripts/
│   └── garmin_auth.py              # One-time Playwright auth — run before the extractor
├── src/
│   ├── config/
│   │   └── logging_config.py       # Centralized logging setup (call once from main.py)
│   ├── extractors/
│   │   └── garmin.py               # Garmin API client: sleep, health telemetry, .FIT runs
│   ├── db/
│   │   ├── transformations.py      # Bronze → Silver: JSON/FIT → Parquet
│   │   └── connection.py           # Silver → Gold: DuckDB views & query interface
│   ├── models/
│   │   ├── biometrics.py           # DailyReadiness, RunSummary, AthleteContext
│   │   └── workouts.py             # Workout plan models — WIP
│   └── agents/
│       └── planner_agent.py        # J.A.R.V.I.S. planner agent — WIP
├── tests/
│   ├── test_extractors.py
│   ├── test_transformations.py
│   ├── test_connection.py
│   └── test_biometrics.py
├── .env.example                    # Template for secrets
├── pyproject.toml
└── main.py                         # System entry point
```

## 🚀 Setup

**Prerequisites:** Python 3.11+, [`uv`](https://github.com/astral-sh/uv)

```bash
# Clone and install dependencies
git clone <repo-url>
cd STARK
uv sync

# Install Playwright's Chromium browser (required for auth)
uv run playwright install chromium

# Configure credentials
cp .env.example .env
# Edit .env with your Garmin email and password
```

`.env` required variables:
```
GARMIN_EMAIL=your@email.com
GARMIN_PASSWORD=yourpassword
```

## ⚙️ Running the Pipeline

### First-time authentication

Garmin's SSO blocks headless/automated login attempts. Run this **once** to authenticate via a real browser and save the session tokens locally:

```bash
uv run python scripts/garmin_auth.py
```

A Chromium window will open — log in manually. Tokens are saved to `~/.garminconnect/` and reused automatically on every subsequent run.

### Data pipeline

Each step can be run independently:

```bash
# 1. Extract raw data from Garmin (Bronze layer)
uv run python src/extractors/garmin.py

# 2. Transform raw data into Parquet (Bronze → Silver)
uv run python src/db/transformations.py

# 3. Query the Gold layer via DuckDB
uv run python src/db/connection.py
```

## 📊 Data Extracted

### Bronze layer (`data/raw/`)

| File pattern | Source | Description |
|---|---|---|
| `sleep_data_YYYY-MM-DD.json` | Garmin API | Raw sleep session data |
| `health_telemetry_YYYY-MM-DD.json` | Garmin API | HRV, body battery, stress, VO2 Max, steps |
| `run_YYYY-MM-DD_<id>.zip` | Garmin API | Original `.FIT` binary file (second-by-second telemetry) |

### Silver layer (`data/processed/`)

| File | Columns |
|---|---|
| `silver_sleep_data.parquet` | `date`, `sleep_time_seconds`, `deep/light/rem/awake_sleep_seconds`, `sleep_score`, `sleep_score_qualifier`, `avg_heart_rate`, `avg_respiration`, `avg_spo2`, `avg_stress`, `sleep_score_feedback` |
| `silver_health_telemetry.parquet` | `date`, `resting_heart_rate`, `avg_stress_level`, `steps`, `active_calories`, `total_distance_meters`, `hrv_weekly_avg`, `hrv_last_night_avg`, `hrv_status`, `body_battery_end`, `training_status_phrase`, `vo2_max` |
| `silver_run_<id>.parquet` | `timestamp`, `distance`, `heart_rate`, `cadence`, `power`, `enhanced_speed`, `enhanced_altitude`, `position_lat`, `position_long`, `temperature`, `vertical_oscillation`, `vertical_ratio`, `stance_time`, `step_length`, ... |

### Gold layer (`data/duckdb/runner_data.db`)

DuckDB views defined in `src/db/connection.py`:

| View | Description |
|---|---|
| `gold_sleep` | Wraps `silver_sleep_data.parquet` |
| `gold_health` | Wraps `silver_health_telemetry.parquet` |
| `gold_runs` | Union of all `silver_run_*.parquet` files with `union_by_name=true` |

## 🧪 Tests

```bash
uv run pytest tests/ -v
```

27 tests covering extractors (Garmin API mocking), transformations (JSON → Parquet), DuckDB connection layer, and Pydantic biometric models.
