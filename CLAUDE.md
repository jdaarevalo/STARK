# STARK — Claude Code Guidelines

## Project overview

S.T.A.R.K. (Smart Training & Athletic Readiness Kernel) is a local data engineering pipeline + multi-agent system for athletic performance optimization. It ingests Garmin biometric data, processes it through a Medallion Architecture (Bronze → Silver → Gold), and exposes it to pydantic-ai LLM agents.

**Package manager:** `uv`. Always use `uv run` and `uv sync`, never `pip`.

---

## Project structure

```
src/
  config/logging_config.py   # Centralized logging — setup_logging() called once from main.py
  extractors/garmin.py       # Garmin API: sleep, health telemetry, .FIT runs
  db/transformations.py      # Bronze → Silver: JSON/FIT → Parquet
  db/connection.py           # Silver → Gold: DuckDB views + query interface (StarkDatabase)
  models/biometrics.py       # Pydantic models: DailyReadiness, RunSummary, AthleteContext
  models/workouts.py         # Pydantic models: workout plans (WIP)
  agents/                    # J.A.R.V.I.S. multi-agent logic (WIP)
data/
  raw/                       # Bronze: Garmin JSONs + .FIT ZIPs (git-ignored)
  processed/                 # Silver: Parquet files (git-ignored)
  duckdb/runner_data.db      # Gold: DuckDB database (git-ignored)
tests/
  test_extractors.py
  test_transformations.py
  test_connection.py
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

### Silver (`data/processed/`)
Parquet files written by `src/db/transformations.py`:
- `silver_sleep_data.parquet` — all dates consolidated in one file
- `silver_health_telemetry.parquet` — all dates consolidated in one file
- `silver_run_<activity_id>.parquet` — one file per activity

When parsing Garmin JSON, always use `data.get("key") or {}` (not just `data.get("key", {})`) to guard against explicit `None` values returned by the API.

FIT files: drop columns starting with `unknown_` — they are undocumented proprietary Garmin fields.

### Gold (`data/duckdb/runner_data.db`)
DuckDB views in `StarkDatabase._setup_views()`:
- `gold_sleep` → `silver_sleep_data.parquet`
- `gold_health` → `silver_health_telemetry.parquet`
- `gold_runs` → `silver_run_*.parquet` with `union_by_name=true`

`StarkDatabase` is a singleton. Reset `StarkDatabase._instance = None` in tests.

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

## Testing

Run tests with: `uv run pytest tests/ -v`

Conventions:
- Use `tmp_path` fixture for all file I/O — never touch `data/raw/` or `data/processed/` in tests
- Mock `RAW_DATA_DIR`, `RAW_DIR`, `PROCESSED_DIR` with `unittest.mock.patch`
- Mock Garmin API clients with `unittest.mock.MagicMock`
- Reset `StarkDatabase._instance = None` in a `autouse=True` fixture for connection tests
- No integration tests against the real Garmin API

---

## Language

- All code, comments, docstrings, log messages, and field descriptions: **English**
- Commit messages: English
