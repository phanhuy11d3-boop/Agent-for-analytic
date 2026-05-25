# Agent Analytic

Generic single-table analytics agent for uploaded or selected PostgreSQL datasets.

The system profiles a selected table, links a natural-language question to physical
columns, builds deterministic SQL evidence, gates unsafe semantic claims, and writes an
HTML report grounded in the executed evidence.

It is not a general "LLM writes any SQL" tool. The current design intentionally separates:

- semantic interpretation and answerability checks
- bounded table preview
- deterministic SQL evidence planning
- report quality validation
- optional LLM summarization over a structured report spec

## Current Scope

- Works on one selected table at a time.
- Supports existing PostgreSQL tables and uploaded CSV/XLS/XLSX datasets.
- Uploaded datasets are stored as `ds_...` tables.
- Uses table profiling, top values, inferred roles, semantic context, and schema linking.
- Generates evidence-bound HTML reports in `reports/`.
- Blocks or downgrades reports when evidence is missing, ambiguous, or only profile-level.

## Important Limitations

- "Any dataset" means arbitrary single-table schemas, not guaranteed perfect analysis for every ambiguous question.
- Multi-table joins are not the core V1 path.
- Semantic/business questions may require confirmed context such as row grain, outcome column, and positive outcome value.
- Numeric "range" wording is not always bucketed automatically; low-cardinality numeric columns may be grouped by exact value.
- Intent classification is heuristic and can still be confused by words that are both business actions and data values.
- Statistical modeling, causality, and feature importance are not proven by this system. Reports are descriptive unless validated by a confirmed outcome.

## Pipeline

```text
User question + selected table
        |
        v
Semantic Agent
  - profile selected table
  - classify intent
  - load/propose semantic context
  - link question terms to columns
  - build explicit aggregate plan when safe
  - decide whether clarification/context is required
        |
        | clarification required
        v
Writer Agent -> context/gap report

        |
        | answerable enough
        v
Data Agent
  - run bounded preview query: SELECT * FROM selected_table
  - build compact data context
        |
        v
Analytics Agent
  - build deterministic evidence plan
  - execute aggregate SQL evidence
  - build report_spec
  - call LLM only to summarize the structured report_spec
        |
        v
Critic Agent
  - deterministic quality checks
  - optional LLM review
  - may loop back to Data Agent up to MAX_CRITIC_ROUNDS
        |
        v
Writer Agent
  - render HTML report
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| Agent orchestration | LangGraph `StateGraph` |
| Web server | FastAPI + WebSocket |
| Database | PostgreSQL/Supabase via `psycopg2-binary` |
| Upload parsing | pandas + openpyxl |
| LLM providers | Groq, 9router, or OpenRouter through `config.invoke_groq(...)` |
| Frontend | Vanilla HTML/CSS/JS |

The default provider in code is `groq`. Set `LLM_PROVIDER=ninerouter` or
`LLM_PROVIDER=openrouter` to use another OpenAI-compatible provider.

## Project Structure

```text
agents/
  semantic_agent.py       Semantic gate, table profiling, schema linking, aggregate plan
  data_agent.py           Bounded preview query for the selected table
  analytics_agent.py      Evidence plan, SQL evidence execution, report spec, LLM summary
  critic_agent.py         Report quality review and optional retry loop
  writer_agent.py         HTML report rendering

tools/
  answerability.py        Determines whether a question can be answered safely
  context_store.py        Loads/saves confirmed semantic context
  data_context.py         Builds compact table/data context for LLM use
  dataset_uploader.py     Creates uploaded `ds_` tables
  evidence_planner.py     Builds SQL evidence items from intent and linked columns
  intent_classifier.py    Heuristic intent classification
  metric_dimension_planner.py
                          Builds explicit metric/dimension/filter aggregate plans
  mschema_builder.py      Metadata schema for selected table
  observation_engine.py   Evidence-bound observations and claims
  query_builder.py        SQL builders for distributions, counts, metrics, trends
  report_planner.py       Builds report_spec and dashboard/report metadata
  report_quality.py       Deterministic report quality checks
  schema_interview.py     Detects missing semantic context
  schema_linker.py        Links question terms and sample values to columns
  schema_provider.py      Dynamic database schema access and cache
  semantic_inference.py   Role inference from column names/types/cardinality
  semantic_layer.py       Semantic layer metadata
  semantic_proposer.py    Proposes table purpose, row grain, outcome, metrics
  sql_executor.py         Read-only SQL execution with row limits/timeouts
  table_profiler.py       Profiles DB tables or row samples
  viz_planner.py          Chart specs from evidence results

web/
  app.py                  FastAPI app, WebSocket analysis, semantic-context APIs
  routers/upload.py       Upload/list/delete table APIs
  static/                 Frontend assets

tests/
  test_analytics.py       Generic analytics/unit tests
  test_db.py              Manual DB smoke script

graph.py                  LangGraph wiring
state.py                  AgentState TypedDict
config.py                 Env/config and LLM provider routing
html_report.py            HTML report builder
main.py                   Uvicorn entry point
```

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL/Supabase database
- One configured LLM provider: Groq, 9router, or OpenRouter

### Install

```powershell
pip install -r requirements.txt
```

### Environment

Create `.env`:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname

# groq | ninerouter | openrouter
LLM_PROVIDER=groq

# Groq
GROQ_API_KEY=gsk_...
GROQ_API_KEYS=gsk_...;gsk_...
GROQ_MODEL=llama-3.3-70b-versatile

# 9router
NINEROUTER_API_KEY=your-key
NINEROUTER_BASE_URL=http://localhost:20128/v1
NINEROUTER_MODEL=kc/deepseek/deepseek-chat

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=deepseek/deepseek-chat-v3-0324:free
OPENROUTER_MODELS=deepseek/deepseek-chat-v3-0324:free

# Optional limits
QUERY_SAMPLE_LIMIT=1000
PROFILE_SAMPLE_LIMIT=1000
SEMANTIC_PROFILE_SAMPLE_LIMIT=300
SEMANTIC_PROPOSAL_USE_LLM=1
ANALYSIS_TIMEOUT_SECONDS=180
```

### Run

```powershell
py main.py
```

Open:

```text
http://localhost:8000
```

## Usage

1. Select an existing table or upload CSV/XLS/XLSX.
2. Ask a natural-language question about the selected table.
3. The app profiles the table, links columns, executes SQL evidence, and writes an HTML report.
4. If semantic context is missing, the report asks for the required context instead of inventing an answer.

Good examples:

```text
Which product category has the highest revenue?
Which study hours value has the highest number of placed students?
Compare average score by program where status is Approved.
Show the frequency distribution of payment installments.
Which segments have the highest churn rate?
```

## API

### WebSocket

```text
WS /ws/analyze
send: {"question": "...", "selected_table": "table_name"}
recv: {"type": "start" | "step" | "heartbeat" | "complete" | "error", ...}
```

### REST

```text
GET    /                         Web UI
GET    /reports/{filename}       Generated report HTML
POST   /api/upload               Upload CSV/XLS/XLSX, creates ds_ table
GET    /api/tables               List tables and row counts
DELETE /api/tables/{name}        Delete uploaded ds_ table only
GET    /api/semantic-context     Get context, gaps, proposal for a table
POST   /api/semantic-context     Save/update semantic context
POST   /api/reanalyze            Re-run report planner on provided rows
```

Upload constraints:

- CSV, XLSX, XLS
- max 50 MB
- max 200 columns

## Evidence And Report Safety

The report is intended to be evidence-bound:

- `Data Agent` returns preview rows only.
- `Analytics Agent` executes deterministic aggregate SQL from `evidence_planner`.
- `report_planner` blocks metric dashboards when no usable aggregate evidence exists.
- `report_quality` flags missing direct answers, empty charts, and missing business evidence.
- `answerability` prevents business/outcome claims when semantic context is missing.

For count-ranking questions with value filters, the system builds SQL like:

```sql
SELECT "group_col" AS label, COUNT(*) AS row_count, COUNT(*) AS value
FROM "table_name"
WHERE "group_col" IS NOT NULL AND "status_col" = 'PositiveValue'
GROUP BY "group_col"
ORDER BY value DESC
LIMIT 10
```

## Tests

Run the main unit tests:

```powershell
py -m unittest tests.test_analytics
```

Run all discoverable tests:

```powershell
py -m unittest discover -s tests
```

Note: `tests/test_db.py` is a DB smoke script and depends on local database contents.

## Development Notes

- Keep table/column logic generic. Do not hardcode domain-specific table names or columns.
- Prefer deterministic SQL builders for evidence over free-form LLM SQL.
- Add randomized or schema-varied tests for planner fixes to avoid overfitting to one dataset.
- When a question is ambiguous, prefer a context-required report over a confident but unsupported answer.
