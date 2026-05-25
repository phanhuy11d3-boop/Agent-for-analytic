# Agent Analytic — Generic Multi-Agent Analysis System

A multi-agent AI system that converts natural language questions into SQL queries, analyzes results, and generates HTML reports — for **any** uploaded dataset, powered by DeepSeek V4 and LangGraph.

---

## Architecture

```
User Question
      │
      ▼
┌──────────────┐    (clarification_required = True)
│Semantic Agent│──────────────────────────────────────────┐
└──────┬───────┘                                          │
       │ (clarification_required = False)                 │
       ▼                                                  ▼
┌──────────────┐   SQL + raw data   ┌──────────────┐  ┌──────────────┐
│  Data Agent  │ ──────────────────▶│AnalyticsAgent│  │ Writer Agent │
└──────┬───────┘        ▲           └──────┬───────┘  └──────▲───────┘
       │                │                  │                 │
       │                │                  ▼                 │
   (no data)            │           ┌──────────────┐         │
       │                └───────────│ Critic Agent │─────────┘
       │             needs_more_data└──────────────┘  approve
       ▼
      END
```

State flows through a LangGraph `StateGraph`. The pipeline starts at `Semantic Agent`. If clarification/metadata is needed, it short-circuits directly to `Writer Agent` to show the Schema Interview / Gaps. Otherwise, it routes to `Data Agent`. The Critic Agent can send feedback to loop `Data Agent` up to 2 times. A conditional edge short-circuits to `END` on SQL error or empty result.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| LLM | DeepSeek V4 Flash via 9router (`oc/deepseek-v4-flash-free`) |
| Agent Framework | LangGraph `StateGraph` |
| Database | Supabase PostgreSQL (`psycopg2-binary`) |
| Web Server | FastAPI + WebSocket |
| Frontend | Vanilla HTML / CSS / JS |

---

## Project Structure

```
Agent-Analytic/
├── agents/
│   ├── semantic_agent.py    # profile table, classify intent, check semantic gaps, request clarification
│   ├── data_agent.py        # NL → SQL → execute → raw_data (1 auto-retry)
│   ├── analytics_agent.py   # raw_data → stats + chi-square + ANOVA → KPIs + insights
│   ├── critic_agent.py      # QA review → approve or request more data (max 2 rounds)
│   └── writer_agent.py      # analytics + raw_data → HTML report
 ├── tools/
│   ├── answerability.py      # evaluate semantic answerability of natural language questions
│   ├── context_store.py     # load/save semantic contexts in semantic_contexts.json
│   ├── data_context.py      # build compact data context and prune for LLM token budget
│   ├── dataset_uploader.py  # upload_dataframe() — creates ds_ tables
│   ├── evidence_planner.py  # plan evidence requirements based on query and intent
│   ├── intent_classifier.py # classify user question intent (descriptive, correlation, etc.)
│   ├── metric_dimension_planner.py # build explicit aggregation plans matching explicit metric and dimensions
│   ├── mschema_builder.py   # build metadata schema combining profile and semantic context
│   ├── observation_engine.py # extract and format structured observations/claims from raw evidence
│   ├── query_builder.py     # help generate or structure SQL queries
│   ├── report_planner.py    # plan reports and convert report specs to analysis metadata
│   ├── report_quality.py    # analyze quality issues in generated report or data context
│   ├── schema_interview.py  # detect semantic gaps and request context/interviews
│   ├── schema_linker.py     # link semantic concepts to physical columns
│   ├── schema_provider.py   # get_schema() — dynamic, cached, invalidatable
│   ├── semantic_inference.py# infer semantics, roles, or relationships from column metadata
│   ├── semantic_layer.py    # construct a semantic layer definition for the table
│   ├── semantic_proposer.py # propose semantic context (purpose, grain, outcomes) based on table profiles
│   ├── sql_executor.py      # execute_sql() — read-only, blocks DDL
│   ├── table_profiler.py    # profile database tables or raw rows to detect types and top values
│   └── viz_planner.py       # plan visualizations based on report spec and data
├── web/
│   ├── app.py               # FastAPI: GET /, WS /ws/analyze, GET /reports/{file}
│   ├── routers/
│   │   └── upload.py        # POST /api/upload, GET /api/tables, DELETE /api/tables/{name}
│   └── static/              # index.html, style.css, app.js
├── tests/
│   ├── test_db.py           # DB connection + sample queries
│   └── test_analytics.py    # Analytics agent unit tests
├── reports/                 # Generated HTML reports (gitignored)
├── config.py                # Load .env, DB/LLM constants, provider routing
├── state.py                 # AgentState TypedDict
├── graph.py                 # LangGraph graph: build + stream
├── html_report.py           # HTML report builder
└── main.py                  # Entry point: py main.py
```

---

## Setup

### Prerequisites

- Python 3.11+
- Supabase PostgreSQL database
- 9router running locally at `http://localhost:20128` (or another OpenAI-compatible provider)

### Installation

```powershell
pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file:

```env
# Database
DATABASE_URL=postgresql://user:password@host:5432/dbname

# LLM Provider (ninerouter | openrouter | groq)
LLM_PROVIDER=ninerouter

# 9router (active)
NINEROUTER_API_KEY=your-key
NINEROUTER_BASE_URL=http://localhost:20128/v1
NINEROUTER_MODEL=oc/deepseek-v4-flash-free

# OpenRouter (fallback)
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODELS=openrouter/free

# Groq (fallback)
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
```

### Run

```powershell
py main.py
```

Open `http://localhost:8000`

---

## Usage

### Web UI

1. Select or upload a dataset (CSV/Excel, max 50 MB)
2. Type a question in natural language (e.g. *"What are the top categories by revenue this month?"* or *"Is there a correlation between region and order value?"*)
3. Click **Analyze** or press `Ctrl+Enter`
4. Watch the agent pipeline execute in real time
5. View the generated HTML report in the right panel

### WebSocket API

```
WS  ws://localhost:8000/ws/analyze
    → send: {"question": "...", "selected_table": "table_name"}
    ← recv: {"type": "start" | "step" | "heartbeat" | "complete" | "error", ...}
```

### REST API

```
POST /api/upload              # Upload CSV/Excel
GET  /api/tables              # List tables with row counts
DELETE /api/tables/{name}     # Delete uploaded table (ds_ prefix only)
GET  /api/semantic-context     # Get semantic context, gaps, and LLM proposal for a table
POST /api/semantic-context     # Save/update semantic context metadata for a table
POST /api/reanalyze           # Re-run analytics on provided rows
```

---

## Agents

| Agent | Input | Output |
|-------|-------|--------|
| Semantic Agent | question + table (any schema) | table profile, intent, semantic proposal, answerability gate, linked columns, aggregate plan — short-circuits to Writer if clarification needed |
| Data Agent | selected table | preview rows via `SELECT *` (bounded by `QUERY_SAMPLE_LIMIT`) |
| Analytics Agent | preview rows + question + semantic context | evidence plan → deterministic SQL queries → report_spec → LLM → KPIs + insights (JSON) |
| Critic Agent | analytics output + question | approve or needs_more_data with feedback (max 2 loops back to Data Agent) |
| Writer Agent | report_spec + analytics | HTML report saved to `reports/` |

