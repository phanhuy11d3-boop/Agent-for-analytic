# Agent Analytic — Project Context

## Stack
- **LLM**: Google Gemini `gemini-2.0-flash` via `langchain-google-genai`
- **Agent framework**: LangGraph `StateGraph` (fixed pipeline, no dynamic supervisor)
- **DB**: Supabase PostgreSQL — `psycopg2-binary`, read-only SELECT only
- **Web**: FastAPI + WebSocket (streaming) + vanilla HTML/CSS/JS
- **Server**: `py main.py`

## File Roles
```
config.py              → load .env, expose DB/LLM constants
state.py               → AgentState TypedDict (shared across all agents)
graph.py               → LangGraph graph: data→analytics→writer, conditional on error
html_report.py         → HTML report builder (generate_report + helpers)
tools/sql_executor.py  → execute_sql(query) → {success, data, columns, row_count}
tools/schema_provider.py → get_schema() → hardcoded schema string (cached)
agents/data_agent.py   → question → SQL → execute → raw_data
agents/analytics_agent.py → raw_data → KPIs + insights (JSON)
agents/writer_agent.py → data+insights → calls html_report.generate_report()
web/app.py             → FastAPI: GET /, WS /ws/analyze, GET /reports/{file}
web/static/            → index.html, style.css, app.js (WebSocket client)
```

## DB Schema — `retail_fraud_transactions` (100k rows)
Key columns: `transaction_id, customer_id, transaction_timestamp, transaction_amount, payment_method` ('Credit Card','Debit Card','PayPal','Google Pay','Apple Pay'), `device_type` ('Mobile','Tablet','Desktop'), `location` ('USA','UK','India','Canada','Australia','Germany'), `merchant_category` ('Fashion','Electronics','Gaming','Travel','Luxury','Groceries'), `fraud_flag` (0/1 — target), `fraud_risk` ('Low','Medium','High')

## DB Connection
`DATABASE_URL` in `.env` — password is URL-encoded, decoded automatically via `urllib.parse.unquote` in `config.py`.

## LangGraph State Keys
`question, sql_query, raw_data, columns, data_error, analytics, report_path, report_filename, steps, final_answer`

## Key Constraints
- `sql_executor` blocks INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE
- Auto-injects `LIMIT 1000` if query has none
- Analytics agent only sends top 30 rows to Gemini (not full 1000)
- Reports saved to `reports/report_{YYYYMMDD_HHMMSS}.html`

## Common Commands
```powershell
py main.py                  # start server (port 8000)
py tests/test_db.py         # test DB connection
```
