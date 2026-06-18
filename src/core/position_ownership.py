"""Ownership guard for one-symbol one-position trading.

Правило:
- по одному символу может быть открыта только одна позиция;
- инстанс стратегии, который открыл позицию, становится владельцем символа;
- остальные инстансы этого символа пропускают входы и не управляют чужой позицией;
- владелец освобождается после закрытия позиции или при sync, если позиции на бирже уже нет.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Optional

from src.config import DATA_DIR
from src.runtime import normalize_symbol_key
from src.utils.logger import info, warning

OWNERS_FILE = os.path.join(DATA_DIR, "position_owners.json")
PENDING_OWNER_GRACE_SECONDS = 120


@dataclass(frozen=True)
class PositionOwner:
    """Владелец активной позиции по символу."""

    symbol: str
    owner_id: str
    strategy: str
    acquired_at: str
    position_id: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


class PositionOwnershipStore:
    """Файловое атомарное хранилище владельцев позиций."""

    def __init__(self, path: str = OWNERS_FILE):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            with open(self.path, "w") as f:
                json.dump({}, f)

    def get_owner(self, symbol: str) -> Optional[PositionOwner]:
        symbol_key = normalize_symbol_key(symbol)
        owners = self._read()
        data = owners.get(symbol_key)
        if not data:
            return None
        return PositionOwner(**data)

    def is_owned_by_other(self, symbol: str, owner_id: str) -> bool:
        owner = self.get_owner(symbol)
        return bool(owner and owner.owner_id != owner_id)

    def try_acquire(
        self,
        symbol: str,
        owner_id: str,
        strategy: str,
        position_id: str = "",
    ) -> tuple[bool, Optional[PositionOwner]]:
        """Атомарно закрепляет символ за owner_id.

        Returns:
            (True, owner) если owner_id получил или уже имел владение.
            (False, existing_owner) если символ занят другим owner_id.
        """
        symbol_key = normalize_symbol_key(symbol)
        with self._locked_data() as owners:
            existing = owners.get(symbol_key)
            if existing and existing.get("owner_id") != owner_id:
                return False, PositionOwner(**existing)

            owner = PositionOwner(
                symbol=symbol_key,
                owner_id=owner_id,
                strategy=strategy,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                position_id=position_id,
            )
            owners[symbol_key] = owner.to_dict()
            return True, owner

    def update_position_id(self, symbol: str, owner_id: str, position_id: str) -> None:
        symbol_key = normalize_symbol_key(symbol)
        with self._locked_data() as owners:
            existing = owners.get(symbol_key)
            if existing and existing.get("owner_id") == owner_id:
                existing["position_id"] = position_id

    def release_if_owner(self, symbol: str, owner_id: str) -> bool:
        """Освобождает символ только если owner_id является владельцем."""
        symbol_key = normalize_symbol_key(symbol)
        with self._locked_data() as owners:
            existing = owners.get(symbol_key)
            if not existing:
                return True
            if existing.get("owner_id") != owner_id:
                return False
            owners.pop(symbol_key, None)
            info(f"🔓 [{symbol_key}] Символ освобождён владельцем {owner_id}")
            return True

    def sync_with_positions(self, positions: Dict) -> None:
        """Удаляет владельцев без реальной позиции и защищает внешние позиции."""
        active_symbols = {normalize_symbol_key(symbol) for symbol, pos in positions.items() if pos}
        with self._locked_data() as owners:
            for symbol in list(owners.keys()):
                if normalize_symbol_key(symbol) not in active_symbols:
                    existing = owners.get(symbol, {})
                    if self._is_recent_pending_owner(existing):
                        continue
                    owners.pop(symbol, None)

            for symbol in active_symbols:
                if symbol not in owners:
                    owners[symbol] = PositionOwner(
                        symbol=symbol,
                        owner_id="external",
                        strategy="EXTERNAL",
                        acquired_at=datetime.now(timezone.utc).isoformat(),
                    ).to_dict()
                    warning(f"🔒 [{symbol}] Найдена позиция без владельца, помечена как external")

    def _read(self) -> Dict[str, Dict[str, str]]:
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _is_recent_pending_owner(owner_data: Dict[str, str]) -> bool:
        if owner_data.get("position_id"):
            return False
        acquired_at = owner_data.get("acquired_at")
        if not acquired_at:
            return False
        try:
            acquired = datetime.fromisoformat(acquired_at)
            if acquired.tzinfo is None:
                acquired = acquired.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - acquired).total_seconds()
            return age < PENDING_OWNER_GRACE_SECONDS
        except Exception:
            return False

    def _locked_data(self):
        return _LockedJsonDict(self.path)


class _LockedJsonDict:
    """Context manager для атомарного read-modify-write JSON dict."""

    def __init__(self, path: str):
        self.path = path
        self.file = None
        self.data: Dict[str, Dict[str, str]] = {}

    def __enter__(self) -> Dict[str, Dict[str, str]]:
        self.file = open(self.path, "r+")
        fcntl.flock(self.file, fcntl.LOCK_EX)
        self.file.seek(0)
        content = self.file.read()
        if content.strip():
            try:
                loaded = json.loads(content)
                self.data = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError:
                warning("[PositionOwnership] Corrupt owners file, rebuilding")
                self.data = {}
        else:
            self.data = {}
        return self.data

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.file.seek(0)
                self.file.truncate()
                json.dump(self.data, self.file, indent=2, default=str)
        finally:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


_store: Optional[PositionOwnershipStore] = None


def get_position_ownership_store() -> PositionOwnershipStore:
    """Singleton store per process."""
    global _store
    if _store is None:
        _store = PositionOwnershipStore()
    return _store
