import json
import asyncio
import time
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.requests import Request

from graph import stream as graph_stream
from agents.analytics_agent import run_analytics
from web.routers.upload import router as upload_router

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR.parent / "reports"

app = FastAPI(title="AI Fraud Analyst")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(upload_router)

REPORTS_DIR.mkdir(exist_ok=True)

_BUILD_VER = str(int(time.time()))  # changes on every server restart → forces browser cache bust


@app.get("/")
async def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('src="/static/app.js"', f'src="/static/app.js?v={_BUILD_VER}"')
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
    loop     = asyncio.get_event_loop()
    analytics = await loop.run_in_executor(None, run_analytics, rows, columns, question)
    return analytics


@app.websocket("/ws/analyze")
async def analyze(websocket: WebSocket):
    await websocket.accept()
    try:
        data           = await websocket.receive_json()
        question       = data.get("question", "").strip()
        selected_table = data.get("selected_table") or None

        if not question:
            await websocket.send_json({"type": "error", "message": "Question cannot be empty"})
            return

        await websocket.send_json({
            "type": "start",
            "message": f"Analyzing: {question}",
        })

        final_state = None
        loop = asyncio.get_event_loop()

        def _run():
            results = []
            for node_name, state in graph_stream(question, selected_table):
                results.append((node_name, state))
            return results

        steps_data = await loop.run_in_executor(None, _run)

        for node_name, state in steps_data:
            steps = state.get("steps", [])
            last_step = steps[-1] if steps else {}
            await websocket.send_json({
                "type":  "step",
                "agent": node_name,
                "step":  last_step,
            })
            final_state = state
            await asyncio.sleep(0.05)

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
