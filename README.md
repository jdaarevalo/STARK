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
* **Data Extraction:** `garminconnect`, `fitparse`
* **Local Data Warehouse:** `DuckDB` (OLAP)
* **Data Validation & Agent Tooling:** `Pydantic`, `pydantic-settings`
* **Environment Management:** `python-dotenv`

## 📂 Repository Structure

```text
S.T.A.R.K./
├── data/                       # Local Data Lakehouse
│   ├── raw/                    # Bronze: Raw Garmin JSONs & .FIT files
│   ├── processed/              # Silver: Cleansed Parquet files
│   └── duckdb/                 # Gold: runner_data.db
├── src/
│   ├── config/                 # Pydantic BaseSettings & Agent Prompts
│   ├── extractors/             # API clients (Garmin, Manual Inputs)
│   ├── db/                     # DuckDB connection & SQL transformations
│   ├── models/                 # Pydantic schemas (Data Contracts)
│   └── agents/                 # Multi-agent logic (J.A.R.V.I.S. & Sub-agents)
├── notebooks/                  # Jupyter notebooks for EDA on .FIT telemetry
├── tests/                      # Pytest suite
├── .env.example                # Template for secrets
├── requirements.txt
└── main.py                     # System entry point