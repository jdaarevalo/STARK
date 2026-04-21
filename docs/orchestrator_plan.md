# Agent Architecture — Design History & Decisions

---

## Current architecture (Phase 2 — Unified agent)

**Status:** Active  
**Entry point:** `src/agents/jarvis_agent.py` → `src/chat/fastapi_app.py` (port 7934)

### Problem with the orchestrator chain

The Phase 1 orchestrator reduced latency vs. calling agents directly via HTTP, but still
produced 3 LLM calls per user message in the common case:

```
User → Orchestrator LLM (decides routing)
           → planner_agent.run()       ← LLM call #2
           → biomechanics_agent.run()  ← LLM call #3
       → Orchestrator LLM (synthesizes)
```

Minimum 6–9 seconds per response. The root cause: sub-agents were full LLMs, not data
functions. The LLM was doing arithmetic that Python could do in milliseconds.

### Solution: aggregation tools + single agent

Replace sub-agent calls with DuckDB-backed Python functions that return pre-computed
summaries. One user message = one LLM call.

```
User message
    │
    ▼
jarvis_agent  (1 LLM call)
    ├── get_readiness_snapshot()      HRV/RHR/BB/Sleep + 7d deltas, soreness
    ├── get_training_load_snapshot()  ATL/CTL/TSB, ACR ratio, risk label
    ├── get_biomechanics_snapshot()   Last 3 runs with drift pre-computed
    └── get_athlete_profile()         Race date, target pace, shoe % life
    │
    ▼
Synthesized plain-text response
```

### Aggregation tool design principle

The tools return interpreted summaries, not raw rows. Example from
`get_training_load_snapshot`:

```python
# Returned to LLM — no arithmetic needed
{
    "ctl_fitness": 42.3,
    "atl_fatigue": 61.7,
    "tsb_form": -19.4,
    "form_label": "High Fatigue",
    "injury_risk": "high",
    "acr_ratio": 1.46,
    "acr_status": "amber — monitor closely",
}
```

This prevents the LLM from having to compute TSB or classify risk levels — it just reads
the label and reasons about it.

### System prompt — panel of experts

J.A.R.V.I.S. operates as three integrated lenses in a single system prompt rather than
three separate agents. The lenses are never addressed as separate voices; they inform a
single synthesized output:

- **Physiologist** — governs session type based on HRV/RHR/BB/soreness signals
- **Biomechanics coach** — reads cadence drift, GCT, VO, VR for form and fatigue signals
- **Sports nutritionist** — triggers carbohydrate/hydration prescriptions based on load

---

## Phase 1 — Orchestrator chain (legacy, kept for reference)

**File:** `src/agents/orchestrator.py`  
**Status:** Not used by `fastapi_app.py`. Available for direct CLI use.

The orchestrator was a pydantic-ai Agent with two tools that invoked sub-agents as full
LLM calls. This was the right intermediate step — it established the single-port entry
point and synthesized responses — but the sub-agent latency was the bottleneck.

It remains in the repo because:
- The specialist agents (`planner_agent`, `biomechanics_agent`) it calls are still valid
  and useful for direct development access on their own ports
- The routing logic in its system prompt is a useful reference for how the domain split
  was originally conceived

---

## Port registry

| Service | Port | Notes |
|---|---|---|
| Streamlit dashboard | 8501 | `streamlit run main.py` |
| FastAPI chat (J.A.R.V.I.S.) | 7934 | `jarvis_agent` — primary chat entry point |
| Planner agent | 7932 | Direct access / development |
| Biomechanics agent | 7933 | Direct access / development |
| Next specialist | 7935 | — |

---

## Adding a new specialist domain

With the unified agent, adding a new domain (e.g. hydration analysis, stress trends) means:

1. Add a new aggregation method to `StarkDatabase` in `connection.py`
2. Add a new `@agent.tool` in `jarvis_agent.py` that calls it
3. Add one bullet to the relevant "lens" in the system prompt, or add a fourth lens
4. No new agent file, no new port, no new LLM call

---

## Open decisions

- **Conversation memory across sessions:** Currently history lives in-process in
  `fastapi_app.py` per session cookie. Cleared on server restart.
- **Data staleness warning:** No mechanism yet to warn the user when last extraction was
  >N days ago. Could be a check inside `get_readiness_snapshot`.
- **Structured output:** `jarvis_agent` returns `str`. If the dashboard ever needs to
  render specific fields (e.g. highlight a risk label), wrap output in a thin Pydantic
  model at that point.
