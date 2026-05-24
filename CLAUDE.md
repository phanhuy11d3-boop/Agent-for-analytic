# Agent Analytic — Project Context

## 🌟 CORE CONSTITUTION (HIẾN PHÁP DỰ ÁN)
1. **Dynamic & Generic System**: Hệ thống được thiết kế để phục vụ phân tích tự động cho **BẤT KỲ** bảng dữ liệu nào được tải lên, không giới hạn nghiệp vụ hay cấu trúc cột.
2. **TUYỆT ĐỐI CẤM HARDCODE**: Nghiêm cấm hardcode tên bảng, tên cột hoặc giả định các khái niệm nghiệp vụ cố định (như mặc định có cột "fraud" hay "transactions") trong các logic xử lý của Agent, Prompt Templates, hay bộ dựng HTML Report. Tất cả thông tin cấu trúc bảng phải được truy vấn động từ database lúc runtime.

## Stack
- **LLM**: DeepSeek V4 Flash via 9router (`oc/deepseek-v4-flash-free`) — OpenAI-compatible local proxy at `http://localhost:20128/v1`
- **Agent framework**: LangGraph `StateGraph` (fixed pipeline with critic feedback loop)
- **DB**: Supabase PostgreSQL — `psycopg2-binary`, read-only SELECT (except `web/routers/upload.py` which creates `ds_` tables)
- **Web**: FastAPI + WebSocket (streaming) + vanilla HTML/CSS/JS
- **Server**: `py main.py`

## File Roles
```
config.py                  → load .env, DB/LLM constants, invoke_groq/invoke_ninerouter/invoke_openrouter
state.py                   → AgentState TypedDict (shared across all agents)
graph.py                   → LangGraph graph: data→analytics→critic→writer, critic feedback loop
html_report.py             → HTML report builder (generate_report + helpers)
tools/sql_executor.py      → execute_sql(query) → {success, data, columns, row_count}
tools/schema_provider.py   → get_schema/get_schema_for_table/get_columns_for_table (cached, invalidatable)
tools/dataset_uploader.py  → upload_dataframe() → creates ds_* tables in DB
agents/data_agent.py       → question → SQL → execute → raw_data (1 auto-retry on SQL error)
agents/analytics_agent.py  → raw_data → stats+chi-square+ANOVA+correlations → KPIs+insights (JSON)
agents/critic_agent.py     → reviews analytics, may send feedback back to data_agent (max 2 rounds)
agents/writer_agent.py     → data+insights → html_report.generate_report()
web/app.py                 → FastAPI: GET /, WS /ws/analyze, GET /reports/{file}, POST /api/reanalyze
web/routers/upload.py      → POST /api/upload, GET /api/tables, DELETE /api/tables/{name}
web/static/                → index.html, style.css, app.js (WebSocket client)
```

## LLM Providers
`LLM_PROVIDER` in `.env` selects the active provider — all routed through `invoke_groq(messages)` in config.py:

| Provider | Env vars | Notes |
|----------|----------|-------|
| `ninerouter` **(active)** | `NINEROUTER_API_KEY`, `NINEROUTER_BASE_URL`, `NINEROUTER_MODEL` | Local 9router proxy |
| `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODELS`, `OPENROUTER_TIMEOUT` | Fallback list of models |
| `groq` | `GROQ_API_KEYS`, `GROQ_MODEL` | Rate-limit retry across keys |

## Default Dataset — `retail_fraud_transactions` (100k rows)
> Schema này chỉ là dataset mẫu mặc định. System hỗ trợ bất kỳ bảng nào — schema được đọc động từ DB lúc runtime.

Key columns: `transaction_id, customer_id, transaction_timestamp, transaction_amount, payment_method` ('Credit Card','Debit Card','PayPal','Google Pay','Apple Pay'), `device_type` ('Mobile','Tablet','Desktop'), `location` ('USA','UK','India','Canada','Australia','Germany'), `merchant_category` ('Fashion','Electronics','Gaming','Travel','Luxury','Groceries'), `fraud_flag` (0/1 — target), `fraud_risk` ('Low','Medium','High')

Uploaded tables use `ds_` prefix and are auto-detected by `schema_provider` (fully dynamic via `information_schema`).

## LangGraph Pipeline
```
data_agent → analytics_agent → critic_agent ─(approve)──→ writer_agent → END
     ↑                               │
     └──────(needs_more_data, ───────┘
              max 2 rounds)
```
- After `data_agent`: if `data_error` or no rows → END
- After `critic_agent`: if `critic_feedback` and `critic_rounds <= MAX_CRITIC_ROUNDS` → loop back to `data_agent`

## LangGraph State Keys
`question, selected_table, sql_query, raw_data, columns, data_error, analytics, report_path, report_filename, steps, final_answer, critic_feedback, critic_rounds`

## Key Constraints
- `sql_executor` blocks INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE
- Auto-injects `LIMIT 1000` if query has none
- Analytics agent computes aggregated stats (not raw rows) before sending to LLM — includes chi-square, ANOVA, Pearson correlations
- Reports saved to `reports/report_{YYYYMMDD_HHMMSS}.html`
- Uploads: max 50 MB, max 200 columns, stored as `ds_{sanitized_name}` tables; call `invalidate_schema_cache()` after

## API Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `WS` | `/ws/analyze` | Main pipeline (streaming, sends `start/step/heartbeat/complete/error`) |
| `GET` | `/reports/{filename}` | Serve generated HTML report |
| `POST` | `/api/upload` | Upload CSV/Excel → creates `ds_` table |
| `GET` | `/api/tables` | List all tables with row counts |
| `DELETE` | `/api/tables/{name}` | Delete uploaded (`ds_`) table |
| `POST` | `/api/reanalyze` | Re-run analytics on provided rows without re-querying DB |

## Common Commands
```powershell
py main.py              # start server (port 8000)
py tests/test_db.py     # test DB connection
```
