import asyncio
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import dashboard, trades, charts, logs, config_routes, journal, chart_data
from .services.file_watcher import FileWatcher
from .ws import manager

logger = logging.getLogger("panel.app")


file_watcher: FileWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global file_watcher

    # Log config paths for diagnostics
    from .config import DATA_DIR, CHARTS_DIR, CONFIG_PATH, PROJECT_ROOT
    logger.info("Config paths:")
    logger.info("  PROJECT_ROOT: %s (exists=%s)", PROJECT_ROOT, PROJECT_ROOT.is_dir())
    logger.info("  CONFIG_PATH: %s (exists=%s)", CONFIG_PATH, CONFIG_PATH.exists())
    logger.info("  DATA_DIR: %s (exists=%s)", DATA_DIR, DATA_DIR.is_dir())
    logger.info("  CHARTS_DIR: %s (exists=%s)", CHARTS_DIR, CHARTS_DIR.is_dir())

    # Check new config system
    config_dir = CONFIG_PATH.parent
    logger.info("  CONFIG_DIR: %s (exists=%s)", config_dir, config_dir.is_dir())
    if config_dir.is_dir():
        logger.info("  active.json exists: %s", (config_dir / "active.json").exists())

    loop = asyncio.get_running_loop()
    file_watcher = FileWatcher(loop=loop)
    file_watcher.set_ws_manager(manager)
    file_watcher.start()
    logger.info("Panel backend started")
    yield
    file_watcher.stop()
    logger.info("Panel backend stopped")


app = FastAPI(title="OpenProducerBot Panel", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions, log them, and return detailed error for debugging."""
    tb = traceback.format_exc()
    logger.error("Unhandled exception on %s %s: %s\n%s", request.method, request.url.path, exc, tb)
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "type": type(exc).__name__,
            "path": request.url.path,
        },
    )


# CORS for Telegram WebApp and direct browser access
# Use PANEL_ALLOWED_ORIGINS env var (comma-separated) or defaults
_allowed_origins = os.getenv(
    "PANEL_ALLOWED_ORIGINS",
    "https://web.telegram.org,https://telegram.org"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Telegram-Init-Data", "X-Web-Token"],
)


# Request logging middleware
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log all HTTP requests for debugging."""
    start_time = time.time()
    path = request.url.path
    method = request.method

    # Skip logging for static assets and websocket
    if path.startswith(("/assets/", "/ws")):
        return await call_next(request)

    logger.info("→ %s %s", method, path)

    try:
        response = await call_next(request)
        duration = (time.time() - start_time) * 1000
        logger.info("← %s %s → %d (%.1fms)", method, path, response.status_code, duration)
        return response
    except Exception as e:
        duration = (time.time() - start_time) * 1000
        logger.error("← %s %s → EXCEPTION: %s (%.1fms)", method, path, e, duration)
        raise


# API routes
app.include_router(dashboard.router)
app.include_router(trades.router)
app.include_router(charts.router)
app.include_router(logs.router)
app.include_router(config_routes.router)
app.include_router(journal.router)
app.include_router(chart_data.router)


@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}


@app.get("/api/debug")
async def debug_info() -> dict:
    """Diagnostic endpoint — returns env and config paths."""
    from .config import DATA_DIR, CHARTS_DIR, CONFIG_PATH, PROJECT_ROOT

    config_dir = CONFIG_PATH.parent
    return {
        "status": "ok",
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "project_root_exists": PROJECT_ROOT.is_dir(),
            "config_path": str(CONFIG_PATH),
            "config_exists": CONFIG_PATH.exists(),
            "data_dir": str(DATA_DIR),
            "data_dir_exists": DATA_DIR.is_dir(),
            "charts_dir": str(CHARTS_DIR),
            "charts_dir_exists": CHARTS_DIR.is_dir(),
            "config_dir": str(config_dir),
            "config_dir_exists": config_dir.is_dir(),
            "active_json_exists": (config_dir / "active.json").exists() if config_dir.is_dir() else False,
        },
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; handle incoming messages if needed
            data = await websocket.receive_text()
            # Client messages (e.g. subscribe) can be handled here in the future
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# Mount static frontend build (must be last so API routes take priority)
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
