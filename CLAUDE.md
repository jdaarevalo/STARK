# STARK — Claude Code Guidelines

## Project overview

S.T.A.R.K. (Smart Training & Athletic Readiness Kernel) is a local data engineering pipeline + multi-agent system for athletic performance optimization. It ingests Garmin biometric data, processes it through a Medallion Architecture (Bronze → Silver → Gold), and exposes it to pydantic-ai LLM agents.

**Package manager:** `uv`. Always use `uv run` and `uv sync`, never `pip`.

---

## Project structure

```
scripts/
  garmin_auth.py             # One-time Playwright auth — run before the extractor
src/
  config/logging_config.py   # Centralized logging — setup_logging() called once from main.py
  config/agents.py           # GoogleModel factory — get_google_model()
  extractors/garmin.py       # Garmin API: sleep, health telemetry, .FIT runs
  db/transformations.py      # Bronze → Silver: JSON/FIT → Parquet
  db/connection.py           # Silver → Gold: DuckDB views + query interface (StarkDatabase)
                             # Aggregation tools: get_readiness_snapshot, get_training_load_snapshot,
                             # get_biomechanics_snapshot (pre-computed for the unified agent)
                             # Also exports: load/save_athlete_config, load/save_daily_input
  models/biometrics.py       # DailyReadiness, RunSummary, AthleteContext, ShoeEntry, BiomechanicsReport
  models/workouts.py         # DailyActionPlan, WorkoutInterval
  agents/jarvis_agent.py     # J.A.R.V.I.S. unified agent — PRIMARY entry point
                             # Hybrid: injects recovery+profile snapshot, exposes 4 tools:
                             #   get_training_load, get_recent_runs, get_health_trend,
                             #   query_athlete_data (SELECT-only SQL against gold views)
  agents/orchestrator.py     # Legacy orchestrator (3-LLM chain) — kept, not used by fastapi_app
  agents/planner_agent.py    # Training planner specialist — port 7932
  agents/biomechanics_agent.py  # Biomechanics specialist — port 7933
  chat/fastapi_app.py        # FastAPI SSE chat UI — port 7934, uses jarvis_agent
data/
  raw/                       # Bronze: Garmin JSONs + .FIT ZIPs (git-ignored)
  processed/                 # Silver: Parquet files (git-ignored)
  duckdb/runner_data.db      # Gold: DuckDB database (git-ignored)
  athlete_config.json        # Athlete profile: LTHR, target pace, shoes (git-ignored)
  daily_inputs.json          # Daily soreness inputs, keyed by date (git-ignored)
main.py                      # Streamlit dashboard — run with: streamlit run main.py
tests/
  test_extractors.py
  test_transformations.py
  test_connection.py         # StarkDatabase singleton + original queries
  test_connection_queries.py # Dashboard queries: intensity, efficiency, load, km_since, persistence
  test_biometrics.py
```

---

## Logging

**Rule: never call `logging.basicConfig()` in any module.**

Every module declares its logger as:
```python
logger = logging.getLogger(__name__ if __name__ != "__main__" else "src.<module.path>")
```

`setup_logging()` is called **once**, at the top of `main.py` (or the `if __name__ == "__main__"` block of standalone scripts), before any other `src/` import:

```python
from src.config.logging_config import setup_logging
setup_logging()
```

For standalone script execution, add `sys.path.insert` at **module level** (before any `src.*` import), and `setup_logging()` inside `__main__`:
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[N]))  # must be before src imports

from src.models.whatever import Something  # now resolves correctly

if __name__ == "__main__":
    from src.config.logging_config import setup_logging
    setup_logging()
```

Log format: `2026-03-21 10:00:00 | INFO     | src.extractors.garmin | message`

---

## File paths

All modules resolve paths relative to `PROJECT_ROOT`, never hardcoded strings:

```python
PROJECT_ROOT = Path(__file__).resolve().parents[N]  # N depends on module depth
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
```

- `src/extractors/` → `parents[2]`
- `src/db/` → `parents[2]`
- `src/config/` → `parents[2]`

Always use `Path.mkdir(parents=True, exist_ok=True)` before writing files.

---

## Data layer conventions

### Bronze (`data/raw/`)
Raw files written by `src/extractors/garmin.py`:
- `sleep_data_YYYY-MM-DD.json`
- `health_telemetry_YYYY-MM-DD.json`
- `run_YYYY-MM-DD_<activity_id>.zip`
- `hydration_YYYY-MM-DD.json` — re-extracted for last 7 days on every sync
- `weight_history_YYYY-MM-DD.json` — 30-day weigh-in range, re-extracted on every sync
- `training_plan_YYYY-MM-DD.json` — active Garmin Coach adaptive plan, re-extracted on every sync
- `race_predictions_YYYY-MM-DD.json` — Garmin 5K/10K/HM/marathon estimates, re-extracted on every sync

### Silver (`data/processed/`)
Parquet files written by `src/db/transformations.py`:
- `silver_sleep_data.parquet` — all dates consolidated in one file
- `silver_health_telemetry.parquet` — all dates consolidated in one file
- `silver_run_<activity_id>.parquet` — one file per activity
- `silver_hydration.parquet` — all dates consolidated in one file
- `silver_weight.parquet` — all weigh-in entries consolidated in one file

When parsing Garmin JSON, always use `data.get("key") or {}` (not just `data.get("key", {})`) to guard against explicit `None` values returned by the API.

FIT files: drop columns starting with `unknown_` — they are undocumented proprietary Garmin fields.

### Gold (`data/duckdb/runner_data.db`)
DuckDB views in `StarkDatabase._setup_views()`:
- `gold_sleep` → `silver_sleep_data.parquet`
- `gold_health` → `silver_health_telemetry.parquet`
- `gold_runs` → `silver_run_*.parquet` with `union_by_name=true`
- `gold_hydration` → `silver_hydration.parquet` (created only if file exists)
- `gold_weight` → `silver_weight.parquet` (created only if file exists)

Non-time-series data (race predictions, training plan) is loaded directly from the most
recent raw JSON via `load_race_predictions()` and `load_training_plan()` — no DuckDB view
needed since they are not queried with SQL.

`StarkDatabase` is a singleton. Reset `StarkDatabase._instance = None` in tests.

The DuckDB connection is **in-memory** (`:memory:`) to allow multiple processes (Streamlit + FastAPI) to run simultaneously without file-lock conflicts. This means `StarkDatabase` cannot persist data across restarts.

Athlete-supplied values that must survive restarts are stored in plain JSON files:
- `data/athlete_config.json` — LTHR, target pace, shoe list (written by `save_athlete_config`)
- `data/daily_inputs.json` — daily soreness keyed by ISO date (written by `save_daily_input`)

Both files are git-ignored. Add them to `.gitignore` if missing.

---

## Pydantic models

Models live in `src/models/`. All field descriptions must be in English — they are passed directly to the LLM as schema context.

Key patterns:
- Use `import datetime` and type fields as `datetime.date` — never `from datetime import date` (clashes with field named `date`)
- Fields that come from the DB but should not be in the LLM payload: `Field(..., exclude=True)`
- Use `@computed_field` for values derived from excluded fields (e.g. `avg_pace_per_km` from `avg_speed_m_s`)
- Provide `from_db(row: dict)` classmethods to handle DB → model conversion (unit changes, type coercion)

```python
# Correct
import datetime
run_date: datetime.date

# Wrong — pydantic field name clashes with type annotation
from datetime import date
date: date
```

---

## Agents

### Primary agent — `jarvis_agent.py`

`src/agents/jarvis_agent.py` is the single active entry point used by `fastapi_app.py`.
It uses a **hybrid architecture**: a lightweight context snapshot is always injected, and
deeper data is fetched on-demand via tools. **Do not add new sub-agent chains** — add
aggregation methods to `StarkDatabase` and expose them as `@agent.tool_plain` functions
in `jarvis_agent.py` instead.

`orchestrator.py` is the legacy 3-LLM routing chain. It is kept but not used by the chat UI.

### Hybrid context architecture

Every request injects a lightweight snapshot (~400 tokens) containing only the data needed
to answer *any* question without extra round-trips:

| Always injected | Rationale |
|---|---|
| `recovery` — HRV, RHR, Body Battery, sleep score + 7d deltas, soreness | Needed for all session recommendations |
| `athlete_profile` — goal, LTHR, target pace, days to race, shoe status | Needed for all planning and pacing advice |
| `weight` — latest kg + 30d delta | Lightweight, used by nutrition lens |
| `garmin_coach_today_tomorrow` — today's and tomorrow's Garmin Coach workouts | Needed for daily session recommendations |
| `garmin_race_predictions` — 5K/10K/HM/marathon Garmin estimates | Static reference, near-zero tokens |

Everything else lives behind a tool and is fetched only when the user's question requires it.

### Deciding where a new metric belongs

When you add a new data source, apply this test before writing code:

**Put it in the injected context if ALL of the following are true:**
1. It's needed to answer the most common daily questions ("should I run?", "how am I recovering?")
2. It's a single scalar or a small flat dict (not a list of rows)
3. It changes every day and is always relevant regardless of what the user asks
4. Adding it costs fewer than ~100 tokens

**Expose it as a tool otherwise** — especially if:
- It's a list of records (runs, trend rows, time-series)
- It's only relevant for specific question types
- It's expensive to compute (requires a heavy DuckDB aggregation)
- The user has to ask a specific question for it to matter

**Practical examples:**

| Metric | Where | Why |
|---|---|---|
| Today's HRV + 7d delta | Context | Single scalar, needed for every session recommendation |
| Last 3 runs biomechanics | Tool (`get_recent_runs`) | List of rows, only needed for run-specific questions |
| ATL/CTL/TSB | Tool (`get_training_load`) | Heavy aggregation, only needed for load/planning questions |
| 30-day HRV trend | Tool (`get_health_trend`) | Time-series, only needed for trend questions |
| Morning vs evening HR | Tool (`query_athlete_data`) | Ad-hoc SQL, narrow use case |
| Latest weight + 30d delta | Context | Two scalars, relevant to nutrition lens |
| Full weight history | Tool (`query_athlete_data`) | List of rows, only for trend questions |
| Today/tomorrow Garmin Coach workouts | Context | 1-2 items, needed for every session recommendation |
| Full Garmin Coach week | Tool (`get_upcoming_workouts`) | List of items, only for weekly planning questions |
| Race predictions (5K/10K/HM/Marathon) | Context | 4 scalars, static reference for all pacing advice |

### Available tools in `jarvis_agent.py`

| Tool | Returns | Use for |
|---|---|---|
| `get_training_load()` | ATL/CTL/TSB snapshot dict | Load/fatigue/injury-risk questions, session planning |
| `get_recent_runs(limit)` | List of N runs with full biomechanics | Last session, run history, post-run nutrition |
| `get_health_trend(days)` | Daily HRV/sleep/RHR rows | Multi-day recovery trend questions |
| `get_upcoming_workouts()` | Full Garmin Coach week schedule | Full week plan, sessions beyond tomorrow |
| `query_athlete_data(sql)` | Up to 100 rows from gold views | Any ad-hoc question not covered above |

`query_athlete_data` accepts SELECT-only SQL. The gold view schema is documented in
`_GOLD_SCHEMA` at the top of `jarvis_agent.py` and embedded in the system prompt.

### Output models always live in `src/models/`

Never define Pydantic output models inside an agent file. Agent files execute `build_agent()` at module level, which instantiates the LLM client and requires the API key. Any file that imports an agent module inherits that cost — even if it only needs a model class.

**Dependency direction is strictly one-way:**
```
src/models/   ←  imported by  ←  src/agents/
```

The reverse is never allowed. A model must never import from an agent.

```python
# Correct — model lives in src/models/, agent imports it
# src/models/biometrics.py
class BiomechanicsReport(BaseModel): ...

# src/agents/biomechanics_agent.py
from src.models.biometrics import BiomechanicsReport
agent = Agent(..., output_type=BiomechanicsReport)

# Wrong — model defined inside the agent file
# src/agents/biomechanics_agent.py
class BiomechanicsReport(BaseModel): ...   # ← blocks reuse, forces API key on import
agent = Agent(..., output_type=BiomechanicsReport)
```

### Agent file structure

Each agent file follows this order:
1. `sys.path.insert` (if run directly)
2. Imports — stdlib, third-party, then `src.*`
3. `logger = logging.getLogger(...)`
4. `SYSTEM_PROMPT` constant (module-level string)
5. `def build_agent() -> Agent` — reads env vars, constructs model and agent
6. `agent = build_agent()` — called once at module level
7. `@agent.tool` functions — each calls `StarkDatabase()` inside the body, never at module level
8. `app = agent.to_web()`
9. `if __name__ == "__main__"` block

### Ports

| Agent | Port | Notes |
|---|---|---|
| `streamlit run main.py` | 8501 | Dashboard + chat iframe |
| `chat/fastapi_app.py` | 7934 | J.A.R.V.I.S. chat — uses `jarvis_agent` (unified, 1 LLM call) |
| `planner_agent.py` | 7932 | Direct access / development |
| `biomechanics_agent.py` | 7933 | Direct access / development |
| Next specialist | 7935 | — |

Assign the next available port when creating a new agent.

### StarkDatabase in tools

Always instantiate `StarkDatabase()` inside the tool function body, never at module level. The singleton pattern makes repeated calls free.

```python
# Correct
@agent.tool
def get_recent_runs(ctx, limit: int = 4) -> list:
    db = StarkDatabase()   # free — returns the existing singleton
    return db.get_recent_runs(limit)

# Wrong — instantiated at module level, breaks test isolation
db = StarkDatabase()

@agent.tool
def get_recent_runs(ctx, limit: int = 4) -> list:
    return db.get_recent_runs(limit)
```

---

## Dashboard

`main.py` is a Streamlit app — the sole entry point for the UI.

```
streamlit run main.py        # starts on port 8501
```

### Layout

| Section | Data source | Key insight |
|---|---|---|
| Sidebar — Daily Readiness | `get_health_trend(7)` | HRV, RHR, Body Battery, Sleep vs 7d avg + soreness slider |
| Load Balance | `get_training_load_history(42)` | ATL (7d EMA) / CTL (42d SMA) / TSB — requires LTHR in Athlete Config |
| Intensity Distribution | `get_weekly_intensity(2)` | % time per HR zone vs 80/20 target |
| Run Efficiency | `get_efficiency_trend(16)` | Pace vs HR on easy runs — aerobic adaptation signal |
| Historical Trends | `get_health_trend(30)` + `get_recent_runs(20)` | HRV/Battery, RHR, Sleep Score, Distance/Pace |

### Caching

All data loaders use `@st.cache_data(ttl=300)`. Call `st.cache_data.clear()` after a sync to force a refresh.

### Athlete Config

Saved to `data/athlete_config.json` via the in-app expander. Fields: `target_race_date`, `target_pace_min_per_km`, `lthr`, `shoes[]`. Load Balance is disabled until `lthr > 0`.

### Tooltips

Every section header renders a `st.popover("ℹ️")` button. Tooltip text lives in the `_TOOLTIPS` dict at the top of `main.py` — edit there to update copy.

---

## Testing

Run tests with: `uv run pytest tests/ -v`

Conventions:
- Use `tmp_path` fixture for all file I/O — never touch `data/raw/` or `data/processed/` in tests
- Mock `RAW_DATA_DIR`, `RAW_DIR`, `PROCESSED_DIR`, `_ATHLETE_CONFIG_PATH`, `_DAILY_INPUT_PATH` with `unittest.mock.patch`
- Mock Garmin API clients with `unittest.mock.MagicMock`
- Reset `StarkDatabase._instance = None` in a `autouse=True` fixture for connection tests
- DuckDB requires at least one matching `silver_run_*.parquet` file to create the `gold_runs` view — write a minimal empty parquet in tests that don't need run data
- No integration tests against the real Garmin API

---

## Language

- All code, comments, docstrings, log messages, and field descriptions: **English**
- Commit messages: English
