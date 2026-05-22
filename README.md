# AI Fraud Analyst — Multi-Agent Data Analysis System

A multi-agent AI system that converts natural language questions into SQL queries, analyzes results, and generates HTML reports — powered by Google Gemini and LangGraph.

---

## Architecture

```
User Question
     │
     ▼
┌─────────────┐     SQL + raw data     ┌──────────────────┐     KPIs + insights     ┌──────────────┐
│  Data Agent │ ──────────────────────▶│ Analytics Agent  │ ──────────────────────▶│ Writer Agent │
│             │                        │                  │                         │              │
│ NL → SQL    │                        │ Insight + KPIs   │                         │ HTML Report  │
│ Execute DB  │                        │ Anomaly detect   │                         │ Save to disk │
└─────────────┘                        └──────────────────┘                         └──────────────┘
                                              │ error → END
```

State flows through a LangGraph `StateGraph`. Pipeline is fixed (Data → Analytics → Writer) with a conditional edge that short-circuits to END on SQL error.

---

## Tech Stack

| Layer       | Technology                  |
|-------------|-----------------------------|
| LLM         | Google Gemini `gemini-2.0-flash` |
| Agent Framework | LangGraph `StateGraph`  |
| Database    | Supabase PostgreSQL (`psycopg2-binary`) |
| Web Server  | FastAPI + WebSocket         |
| Frontend    | Vanilla HTML / CSS / JS     |

---

## Project Structure

```
Agent-Analytic/
├── agents/
│   ├── data_agent.py        # NL → SQL → execute → raw_data
│   ├── analytics_agent.py   # raw_data → KPIs + insights (JSON)
│   └── writer_agent.py      # data + insights → HTML report
├── tools/
│   ├── sql_executor.py      # execute_sql() — read-only, blocks DDL
│   └── schema_provider.py   # get_schema() — hardcoded schema string
├── web/
│   ├── app.py               # FastAPI: GET /, WS /ws/analyze, GET /reports/{file}
│   └── static/              # index.html, style.css, app.js
├── tests/
│   └── test_db.py           # DB connection + sample queries
├── reports/                 # Generated HTML reports (gitignored)
├── config.py                # Load .env, expose DB/LLM constants
├── state.py                 # AgentState TypedDict
├── graph.py                 # LangGraph graph: build + stream
├── html_report.py           # HTML report builder
├── main.py                  # Entry point: py main.py
├── .env.example             # Environment variable template
└── requirements.txt
```

---

## Setup

### Prerequisites

- Python 3.11+
- A Supabase PostgreSQL database
- Google Gemini API key

### Installation

```powershell
pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```powershell
copy .env.example .env
```

| Variable       | Description                              |
|----------------|------------------------------------------|
| `DATABASE_URL` | PostgreSQL connection string (URL-encoded password supported) |
| `GOOGLE_API_KEY` | Google Gemini API key                  |
| `GEMINI_MODEL` | Model name (default: `gemini-2.0-flash`) |

### Run

```powershell
py main.py
```

Open `http://localhost:8000`

---

## Usage

### Web UI

1. Type a question in the input box (e.g. *"What is the fraud rate by payment method?"*)
2. Click **Analyze** or press `Ctrl+Enter`
3. Watch the agent pipeline execute in real time
4. View the generated HTML report in the right panel

### WebSocket API

```
WS  ws://localhost:8000/ws/analyze
    → send: {"question": "..."}
    ← recv: {"type": "start" | "step" | "complete" | "error", ...}
```

---

## Agents

| Agent            | Input                  | Output                              |
|------------------|------------------------|-------------------------------------|
| Data Agent       | question + DB schema   | SQL query + raw data rows           |
| Analytics Agent  | raw data + question    | KPIs, insights, anomalies (JSON)    |
| Writer Agent     | analytics + raw data   | HTML report saved to `reports/`     |
