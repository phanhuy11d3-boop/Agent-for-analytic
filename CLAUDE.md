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
graph.py                   → LangGraph graph: semantic→data→analytics→critic→writer, loops/routing
html_report.py             → HTML report builder (generate_report + helpers)
tools/answerability.py     → evaluate semantic answerability of natural language questions
tools/context_store.py     → load/save semantic contexts (metadata) in semantic_contexts.json
tools/data_context.py      → build compact data context and prune for LLM token budget
tools/dataset_uploader.py  → upload_dataframe() → creates ds_ tables in DB
tools/evidence_planner.py  → plan the evidence requirements based on user query and intent
tools/intent_classifier.py → classify user question intent (descriptive, correlation, etc.)
tools/metric_dimension_planner.py → build explicit aggregation plans matching explicit metric and dimensions
tools/mschema_builder.py   → build metadata schema combining table profile and semantic context
tools/observation_engine.py → extract and format structured observations/claims from raw evidence results
tools/query_builder.py     → help generate or structure SQL queries
tools/report_planner.py    → plan reports and convert report specifications into analysis metadata
tools/report_quality.py    → analyze quality issues in generated report or data context
tools/schema_interview.py  → detect semantic gaps and request context/interviews
tools/schema_linker.py     → link semantic concepts to physical columns
tools/schema_provider.py   → get_schema/get_schema_for_table/get_columns_for_table (cached, invalidatable)
tools/semantic_inference.py → infer semantics, roles, or relationships from column metadata
tools/semantic_layer.py    → construct a semantic layer definition for the table
tools/semantic_proposer.py → propose semantic context (purpose, grain, outcomes) based on table profiles and LLM
tools/sql_executor.py      → execute_sql(query) → {success, data, columns, row_count}
tools/table_profiler.py    → profile database tables or raw rows to detect data types, top values
tools/viz_planner.py       → plan visualizations based on report spec and data
agents/semantic_agent.py   → profile table, classify intent, propose semantic context, evaluate answerability, link columns, plan aggregations — gate clarification
agents/data_agent.py       → run preview query (SELECT * with sample limit) → raw_data
agents/analytics_agent.py  → build evidence plan → execute deterministic SQL queries → build report_spec → LLM → KPIs+insights (JSON)
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

## Dataset Support
> System hỗ trợ **bất kỳ bảng dữ liệu nào** — schema được đọc động từ `information_schema` lúc runtime. Không có bảng nào được giả định trước.

- Bảng có sẵn trong DB: truy vấn trực tiếp qua schema provider
- Bảng upload: dùng prefix `ds_` (CSV/Excel, tối đa 50 MB, 200 cột), tự động xuất hiện sau khi gọi `invalidate_schema_cache()`
- Mọi column name, kiểu dữ liệu, top values đều được detect tự động bởi `table_profiler` và `schema_provider`

## LangGraph Pipeline
```
semantic_agent ─(clarification_required=False)──▶ data_agent ──▶ analytics_agent ──▶ critic_agent ─(approve)──▶ writer_agent ──▶ END
     │                                                │                                    │
     │ (clarification_required=True)           (error / no rows)              (needs_more_data, ≤2 rounds)
     ▼                                               ▼                                    │
writer_agent ──▶ END                               END             ◀────────────────────────┘
                                                              (loop back to data_agent)
```
- After `semantic_agent`: if `clarification_required=True` → thẳng đến `writer_agent` để trình bày schema gaps.
- After `data_agent`: if `data_error` or no rows → END.
- After `critic_agent`: if `critic_feedback` and `critic_rounds <= MAX_CRITIC_ROUNDS` → loop back to `data_agent`.

## LangGraph State Keys
`question`, `selected_table`, `sql_query`, `raw_data`, `columns`, `data_error`, `analytics`, `table_profile`, `data_context`, `llm_data_context`, `intent`, `report_spec`, `linked_columns`, `explicit_aggregate_plan`, `evidence_plan`, `evidence_results`, `semantic_context`, `semantic_proposal`, `mschema`, `semantic_layer`, `semantic_gaps`, `answerability`, `output_type`, `clarification_required`, `evidence_level`, `warnings`, `quality_issues`, `row_count_total`, `report_path`, `report_filename`, `steps`, `final_answer`, `critic_feedback`, `critic_rounds`

## Key Constraints
- `sql_executor` blocks INSERT/UPDATE/DELETE/DROP/TRUNCATE/ALTER/CREATE
- Auto-injects `LIMIT {QUERY_SAMPLE_LIMIT}` (default 1000) if query has none; query timeout 30s
- `data_agent` runs a preview query (`SELECT *` bounded by sample limit), không sinh SQL từ question
- `analytics_agent` chạy `evidence_planner` (deterministic SQL aggregations) trước khi gửi LLM — LLM chỉ nhận report_spec đã tổng hợp, không nhận raw rows
- `SEMANTIC_PROPOSAL_USE_LLM=1` (default) → semantic_agent gọi LLM để propose context; set `0` để skip LLM ở bước này
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
| `GET` | `/api/semantic-context` | Get semantic context, gaps, and LLM proposal for a table |
| `POST` | `/api/semantic-context` | Save/update semantic context metadata for a table |
| `POST` | `/api/reanalyze` | Re-run analytics on provided rows without re-querying DB |

## Common Commands
```powershell
py main.py              # start server (port 8000)
py tests/test_db.py     # test DB connection
```
