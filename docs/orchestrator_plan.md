# Orchestrator — Design & Work Plan

**Status:** Phase 1 complete — MVP running  
**Supersedes:** Manual per-agent execution on separate ports

---

## Context

STARK currently has two specialist agents (`planner_agent`, `biomechanics_agent`) each
running on their own port and started separately. The goal is a single entry point that
understands the user's intent, routes to the right specialist(s), and synthesizes a
coherent response — without the user changing ports or execution contexts.

Planned future specialists: hydration, stress/HRV analysis, training theory.

---

## Architecture decision

**Chosen: In-process orchestrator with per-specialist tools.**

The orchestrator is itself a pydantic-ai `Agent`. Each specialist is called via a tool
function that invokes `specialist_agent.run(contextual_prompt)` directly in Python —
no HTTP, no subprocesses, one port.

```
User input
    │
    ▼
Orchestrator Agent  (LLM decides routing)
    ├── tool: consult_biomechanics_agent(focus)
    ├── tool: consult_planner_agent(context)
    ├── tool: consult_hydration_agent(...)       ← future
    ├── tool: consult_stress_agent(...)          ← future
    └── tool: consult_training_theory_agent(...) ← future
    │
    ▼
Synthesized response → Starlette chat (port 7931) + CLI REPL
```

### Why not a generic `delegate(agent_name, prompt)` tool

A single generic tool forces the LLM to reason about available agents from the system
prompt alone, with no schema-level guidance. Per-specialist tools give the LLM explicit
docstrings describing *when* and *why* to use each one — much better routing quality.

### Why not multi-process / HTTP

Unnecessary complexity for a local personal tool. All agents share the same process and
API key. HTTP would add startup dependencies and latency with zero benefit at this scale.

---

## Key design decisions

### Agent initialization — eager at orchestrator startup

All specialists are built when the orchestrator module loads. Startup is slightly slower
but avoids lazy-init complexity. Acceptable for a personal tool with a single user.

```python
# orchestrator.py (module level)
from src.agents.biomechanics_agent import agent as biomechanics_agent
from src.agents.planner_agent      import agent as planner_agent
```

### Orchestrator output type — `str` (free text)

The orchestrator is conversational. Structured output adds friction without benefit at
the routing layer. Specialists already enforce structure via their own output models.
If the UI later needs to show which agents were called, add a thin wrapper model then.

### Context passed to sub-agents

Each tool constructs a self-contained prompt that includes:
- The user's intent (paraphrased from the orchestrator's understanding)
- Today's date
- Any relevant constraints extracted from the conversation

Sub-agents are stateless; the orchestrator holds conversation history.

### Routing logic lives in the system prompt

The orchestrator's system prompt describes each available agent, its domain, and the
signals that indicate it should be called. As new agents are added:
1. Add import + tool function to `orchestrator.py`
2. Add one bullet to the orchestrator's system prompt describing the new agent
3. No other changes required to existing code

---

## Port registry (updated)

| Agent                  | Port | Notes                        |
|------------------------|------|------------------------------|
| `orchestrator.py`      | 7931 | Primary entry point          |
| `planner_agent.py`     | 7932 | Direct access / development  |
| `biomechanics_agent.py`| 7933 | Direct access / development  |
| Next specialist        | 7934 | —                            |

---

## File plan

### New files
| File | Purpose |
|------|---------|
| `src/agents/orchestrator.py` | Central router agent, dual interface |

### Modified files
| File | Change |
|------|--------|
| `CLAUDE.md` | Add orchestrator port (7931) to port registry |

### No changes needed
`planner_agent.py` and `biomechanics_agent.py` already expose `agent` at module level.
The orchestrator imports and calls them directly.

---

## Implementation — Phase 1 (MVP)

### Step 1 — `src/agents/orchestrator.py`

Structure following the agent file convention from CLAUDE.md:

```python
from src.agents.biomechanics_agent import agent as biomechanics_agent
from src.agents.planner_agent      import agent as planner_agent

SYSTEM_PROMPT = """
You are J.A.R.V.I.S., the master coordination layer of S.T.A.R.K.
Your job is to understand the athlete's query and delegate to the right
specialist(s). You synthesize their outputs into a single, coherent response.

Available specialists — call the ones relevant to the query:
  - consult_biomechanics_agent: running mechanics, cadence, vertical oscillation,
    ground contact time, stride length, fatigue drift, efficiency trends.
    Use when: user asks about how they ran, form, technique, mechanics.
  - consult_planner_agent: daily training plan, readiness, rest vs. train decision,
    workout prescription, hydration, nutrition for a session.
    Use when: user asks what to do today/tomorrow or wants a training plan.

Rules:
  - Call only the specialists needed. Do not call all of them for every query.
  - If a query spans multiple domains (e.g. "how did I run and what should I do
    tomorrow?"), call all relevant specialists and synthesize their outputs.
  - Never invent data. If a specialist returns no data, say so clearly.
  - Tone: direct, analytical, slightly witty — J.A.R.V.I.S. from Iron Man.
"""

# Tool: consult_biomechanics_agent(focus)
# Tool: consult_planner_agent(target_date)

# Starlette app on port 7931
# CLI REPL via __main__
```

### Step 2 — CLI REPL

```bash
uv run python src/agents/orchestrator.py
# >>> Analyze my last 3 runs
# >>> What should I do tomorrow?
# >>> exit
```

Single-shot mode:
```bash
uv run python src/agents/orchestrator.py "how did I run this week?"
```

### Step 3 — Update CLAUDE.md port registry

Add `orchestrator.py → 7931` to the ports table.

---

## Implementation — Phase 2 (future agents)

Each new specialist follows the same registration pattern:

```
1. Define output model in src/models/
2. Create src/agents/<name>_agent.py following CLAUDE.md agent structure
3. In orchestrator.py:
   a. Add import
   b. Add @orchestrator.tool function with clear docstring
   c. Add one bullet to SYSTEM_PROMPT describing when to use it
```

### Planned specialists

| Agent | Domain | Key inputs |
|-------|--------|------------|
| `hydration_agent` | Daily hydration targets | Weather, session duration, body weight |
| `stress_agent` | HRV/stress trend analysis | HRV history, body battery, sleep quality |
| `training_theory_agent` | Periodization, load planning | Weekly volume, fitness trends, race date |

---

## Open questions / future decisions

- **Conversation memory**: Should the orchestrator remember previous sessions, or start
  fresh each time? (Currently: fresh each time, history within one session only.)
- **Data staleness detection**: Should the orchestrator warn the user when the last
  extraction was more than N days ago? (Low priority, manual pipeline is acceptable for now.)
- **Parallel specialist calls**: For multi-domain queries, call specialists concurrently
  (`asyncio.gather`) to reduce latency. Implement once there are 3+ specialists being
  called simultaneously.
