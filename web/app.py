import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import asyncio
import time
import threading
import queue
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.requests import Request

from config import SEMANTIC_PROFILE_SAMPLE_LIMIT
from graph import stream as graph_stream
from web.routers.upload import router as upload_router
from tools.context_store import load_context, save_context
from tools.intent_classifier import classify_intent
from tools.mschema_builder import build_mschema
from tools.report_planner import analytics_from_report_spec, build_report_spec
from tools.schema_interview import detect_semantic_gaps
from tools.table_profiler import profile_from_rows, profile_table

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR.parent / "reports"

app = FastAPI(title="AI Data Analyst")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(upload_router)

REPORTS_DIR.mkdir(exist_ok=True)


def _asset_version(filename: str) -> str:
    path = STATIC_DIR / filename
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return "dev"

_BUILD_VER = str(int(time.time()))  # changes on every server restart → forces browser cache bust


@app.get("/")
async def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        "/static/style.css?v=maxxem-ui-20260524-2",
        f"/static/style.css?v={_asset_version('style.css')}",
    )
    html = html.replace(
        "/static/app.js?v=maxxem-ui-20260524-2",
        f"/static/app.js?v={_asset_version('app.js')}",
    )
    html = html.replace(
        'href="/static/style.css"',
        f'href="/static/style.css?v={_asset_version("style.css")}"',
    )
    html = html.replace(
        'src="/static/app.js"',
        f'src="/static/app.js?v={_asset_version("app.js")}"',
    )
    return HTMLResponse(html)


@app.get("/reports/{filename}")
async def serve_report(filename: str):
    filepath = REPORTS_DIR / filename
    if not filepath.exists():
        return HTMLResponse("<h2>Report not found</h2>", status_code=404)
    return FileResponse(str(filepath), media_type="text/html")


@app.post("/api/reanalyze")
async def reanalyze(request: Request):
    payload  = await request.json()
    rows     = payload.get("rows", [])
    columns  = payload.get("columns", [])
    question = payload.get("question", "")
    profile = profile_from_rows(rows, columns)
    intent = classify_intent(question)
    spec = build_report_spec(question, intent, profile, len(rows))
    return analytics_from_report_spec(spec)


@app.get("/api/semantic-context")
async def get_semantic_context(table: str, question: str = ""):
    if not table:
        raise HTTPException(status_code=400, detail="table is required")

    profile = profile_table(table, sample_limit=SEMANTIC_PROFILE_SAMPLE_LIMIT)
    if not profile.get("success"):
        raise HTTPException(status_code=404, detail=profile.get("error") or "table could not be profiled")

    context = load_context(table, profile)
    intent = classify_intent(question or "summarize this dataset")
    mschema = build_mschema(profile, context)
    gaps = detect_semantic_gaps(question, intent, profile, context)
    return {
        "context": context,
        "mschema": mschema,
        "gaps": gaps.get("gaps", []),
        "clarification_required": gaps.get("clarification_required", False),
    }


@app.post("/api/semantic-context")
async def post_semantic_context(request: Request):
    payload = await request.json()
    table = payload.get("table_name") or payload.get("table")
    context = payload.get("context") or {}
    if not table:
        raise HTTPException(status_code=400, detail="table_name is required")

    profile = profile_table(table, sample_limit=SEMANTIC_PROFILE_SAMPLE_LIMIT)
    if not profile.get("success"):
        raise HTTPException(status_code=404, detail=profile.get("error") or "table could not be profiled")

    saved = save_context(table, profile, context)
    mschema = build_mschema(profile, saved)
    return {"context": saved, "mschema": mschema}


@app.websocket("/ws/analyze")
async def analyze(websocket: WebSocket):
    await websocket.accept()
    try:
        data           = await websocket.receive_json()
        question       = data.get("question", "").strip()
        selected_table = data.get("selected_table") or None
        try:
            print(f"[WebSocket] Received - question: '{question}', selected_table: '{selected_table}'")
        except Exception:
            pass

        if not question:
            await websocket.send_json({"type": "error", "message": "Question cannot be empty"})
            return

        await websocket.send_json({
            "type": "start",
            "message": f"Analyzing: {question}",
        })

        final_state = None
        step_queue: queue.Queue = queue.Queue()

        def _run_stream():
            try:
                for node_name, state in graph_stream(question, selected_table):
                    step_queue.put(("step", node_name, state))
                step_queue.put(("done", None, None))
            except Exception as exc:
                step_queue.put(("error", None, exc))

        worker = threading.Thread(target=_run_stream, daemon=True)
        worker.start()

        while True:
            try:
                kind, node_name, payload = await asyncio.to_thread(step_queue.get, True, 1)
            except queue.Empty:
                await websocket.send_json({"type": "heartbeat", "message": "Still working..."})
                continue

            if kind == "done":
                break
            if kind == "error":
                await websocket.send_json({"type": "error", "message": str(payload)})
                return

            state = payload
            steps = state.get("steps", [])
            last_step = steps[-1] if steps else {}
            await websocket.send_json({
                "type":  "step",
                "agent": node_name,
                "step":  last_step,
            })
            final_state = state
            await asyncio.sleep(0)

        if final_state:
            report_filename = final_state.get("report_filename")
            await websocket.send_json({
                "type":            "complete",
                "summary":         final_state.get("final_answer", ""),
                "report_url":      f"/reports/{report_filename}" if report_filename else None,
                "report_filename": report_filename,
                "data_error":      final_state.get("data_error"),
            })
        else:
            await websocket.send_json({"type": "error", "message": "No output from agents"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
