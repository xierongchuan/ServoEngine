
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..config import CHARTS_DIR
from ..services.auth import get_current_user, validate_auth_string
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/charts", tags=["charts"])
reader = DataReader()


@router.get("/list")
async def list_charts(_user: dict = Depends(get_current_user)) -> list[dict]:
    return reader.list_charts()


@router.get("/{filename}")
async def get_chart(
    filename: str,
    auth: str = Query(alias="auth", default=""),
    token: str = Query(alias="token", default=""),
):
    """Serve chart image. Auth is passed via query param since <img src> cannot send headers.

    Supports both Telegram initData (?auth=) and web token (?token=).
    """
    # Use token if provided, otherwise fall back to auth
    auth_param = token or auth

    # Validate auth from query parameter
    validate_auth_string(auth_param)

    # Sanitize: only allow simple filenames (no path traversal)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = CHARTS_DIR / filename
    if not path.is_file() or path.suffix.lower() != ".png":
        raise HTTPException(status_code=404, detail="Chart not found")

    return FileResponse(path, media_type="image/png")
