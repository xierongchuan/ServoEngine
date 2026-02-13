from fastapi import APIRouter, Depends, HTTPException

from ..services.auth import get_current_user
from ..services.data_reader import DataReader

router = APIRouter(prefix="/api/journal", tags=["journal"])
reader = DataReader()


@router.get("")
async def get_journal(_user: dict = Depends(get_current_user)) -> dict:
    return reader.read_journal()


@router.get("/{symbol}")
async def get_symbol_journal(
    symbol: str, _user: dict = Depends(get_current_user)
) -> dict:
    journal = reader.read_journal()
    data = journal.get(symbol)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No journal entries for {symbol}")
    return data
