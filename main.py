import os

import uvicorn

if __name__ == "__main__":
    reload_enabled = os.getenv("MAXXEM_RELOAD", "0").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=reload_enabled)
