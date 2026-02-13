import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/config", tags=["config"])
reader = DataReader()


@router.get("")
async def get_config(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_config()


@router.put("")
async def update_config(request: Request, _user: dict = Depends(get_current_user)) -> dict:
    try:
        body = await request.body()
        new_config = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(new_config, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")

    try:
        reader.write_config(new_config)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")

    return {"status": "ok"}
