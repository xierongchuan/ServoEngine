import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import dashboard, trades, charts, logs, config_routes, journal
from .services.file_watcher import FileWatcher
from .ws import manager

logger = logging.getLogger("panel.app")

file_watcher: FileWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global file_watcher
    loop = asyncio.get_running_loop()
    file_watcher = FileWatcher(loop=loop)
    file_watcher.set_ws_manager(manager)
    file_watcher.start()
    logger.info("Panel backend started")
    yield
    file_watcher.stop()
    logger.info("Panel backend stopped")


app = FastAPI(title="OpenProducerBot Panel", lifespan=lifespan)

# CORS for Telegram WebApp
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://web.telegram.org", "https://telegram.org", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(dashboard.router)
app.include_router(trades.router)
app.include_router(charts.router)
app.include_router(logs.router)
app.include_router(config_routes.router)
app.include_router(journal.router)


@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}


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
