import asyncio
import logging
import os
import time
import traceback
import uuid
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import dashboard, trades, charts, logs, config_routes, journal, chart_data, runtime, execution, ai
from .services.file_watcher import FileWatcher
from .services.auth import authenticate, get_current_user
from .services.token_store import get_token_store
from .ws import manager

logger = logging.getLogger("panel.app")


file_watcher: FileWatcher | None = None
WS_AUTH_CHECK_SECONDS = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    global file_watcher

    # Log config paths for diagnostics
    from .config import DATA_DIR, CHARTS_DIR, CONFIG_PATH, PROJECT_ROOT
    from src.utils.logger import configure_logging, protect_logging_handlers

    configure_logging(data_dir=DATA_DIR)
    protect_logging_handlers()
    logger.info("Пути конфигурации:")
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
    logger.info("✅ Backend панели запущен")
    yield
    file_watcher.stop()
    logger.info("Backend панели остановлен")


app = FastAPI(title="Servo Engine Panel", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Не раскрывает исключения, пути и потенциальные секреты клиенту или логам."""
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    frames = traceback.extract_tb(exc.__traceback__)[-6:]
    location = " → ".join(f"{Path(frame.filename).name}:{frame.lineno} ({frame.name})" for frame in frames)
    route = getattr(request.scope.get("route"), "path", "неизвестен")
    from src.utils.logger import log_context
    with log_context(category="panel", request_id=request_id):
        logger.error("❌ Внутренняя ошибка панели; request_id=%s, маршрут=%s, тип=%s, место=%s",
                     request_id, route, type(exc).__name__, location)
    return JSONResponse(status_code=500,
                        content={"detail": "Внутренняя ошибка панели", "request_id": request_id},
                        headers={"X-Request-ID": request_id})


# WS использует тот же явный список Origin, что HTTP CORS; wildcard запрещён.
def _configured_origins() -> list[str]:
    values = os.getenv("PANEL_ALLOWED_ORIGINS", "").split(",")
    values.append(os.getenv("TELEGRAM_PANEL_URL", ""))
    origins = []
    for value in values:
        parsed = urlparse(value.strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.username:
            origins.append(f"{parsed.scheme}://{parsed.netloc}")
    if os.getenv("PANEL_ALLOW_LOCAL_DEV_AUTH") == "1":
        origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
    return list(set(origins))


_allowed_origins = _configured_origins()

# Снимки свечей и индикаторов велики; уменьшаем передачу через туннель без потери истории.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
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
    """Логирует только маршрут без query, заголовков и тела запроса."""
    request.state.request_id = uuid.uuid4().hex
    start_time = time.monotonic()
    path = request.url.path
    if path.startswith("/api/"):
        path = "/api/" + path.split("/")[2]
    method = request.method

    # Skip logging for static assets and websocket
    if path.startswith(("/assets/", "/ws")):
        return await call_next(request)

    from src.utils.logger import log_context
    with log_context(category="panel", request_id=request.state.request_id):
        logger.info("→ %s %s", method, path)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            duration = (time.monotonic() - start_time) * 1000
            route = getattr(request.scope.get("route"), "path", path)
            logger.info("← %s %s → %d (%.1fms)", method, route, response.status_code, duration)
            return response
        except Exception as exc:
            return await global_exception_handler(request, exc)


@app.middleware("http")
async def frontend_cache_control_middleware(request: Request, call_next):
    """Не даёт Telegram WebView держать старую сборку панели после rebuild."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# API routes
app.include_router(dashboard.router)
app.include_router(trades.router)
app.include_router(charts.router)
app.include_router(logs.router)
app.include_router(config_routes.router)
app.include_router(ai.router)
app.include_router(journal.router)
app.include_router(chart_data.router)
app.include_router(runtime.router)
app.include_router(execution.router)


@app.get("/api/health")
async def health_check() -> dict:
    return {"status": "ok"}


@app.get("/api/debug")
async def debug_info(_user: dict = Depends(get_current_user)) -> dict:
    """Диагностика только при явном включении и после авторизации."""
    if os.getenv("PANEL_ENABLE_DEBUG") != "1":
        raise HTTPException(404, "Не найдено")
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


@app.post("/api/ws-ticket")
async def create_ws_ticket(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    if _user.get("auth_method") == "local_dev":
        raise HTTPException(403, "Для WS требуется полноценная авторизация")
    credentials = {"init_data": request.headers.get("X-Telegram-Init-Data", ""),
                   "web_token": request.headers.get("X-Web-Token", "")}
    try:
        return {"ticket": get_token_store().issue_ws_ticket(credentials), "expires_in": 30}
    except ValueError:
        raise HTTPException(429, "Повторите подключение позже")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Ни accept, ни регистрация в manager не происходят до проверки Origin и сессии.
    if websocket.headers.get("origin") not in _allowed_origins:
        await websocket.close(code=1008)
        return
    ticket = websocket.query_params.get("ticket", "")
    if not ticket or len(ticket) > 256:
        await websocket.close(code=1008)
        return
    try:
        credentials = get_token_store().consume_ws_ticket(ticket)
        if not credentials:
            await websocket.close(code=1008)
            return
        authenticate(**credentials)
    except (HTTPException, OSError, ValueError):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    last_check = time.monotonic()
    try:
        while True:
            # Пустой либо шумный клиент не может отложить проверку срока/отзыва.
            remaining = max(0.01, WS_AUTH_CHECK_SECONDS - (time.monotonic() - last_check))
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=remaining)
            except asyncio.TimeoutError:
                pass
            if time.monotonic() - last_check >= WS_AUTH_CHECK_SECONDS:
                try:
                    authenticate(**credentials)
                except HTTPException:
                    await websocket.close(code=4401, reason="Сессия истекла или отозвана")
                    break
                last_check = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


# Mount static frontend build (must be last so API routes take priority).
# В контейнере frontend собирается в /app/frontend/dist, а /app/src может быть
# примонтирован для backend-кода. Поэтому сначала берём build из image.
frontend_dist = Path("/app/frontend/dist")
if not frontend_dist.is_dir():
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
