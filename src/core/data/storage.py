"""Атомарное хранилище JSON с блокировкой — дедупликация fcntl+json."""

import fcntl
import json
import os
from typing import Any, Optional


class AtomicJsonStore:
    """Атомарная запись JSON с блокировкой."""

    def __init__(self, filepath: str):
        self.filepath = filepath

    def read(self, default: Any = None) -> Any:
        if not os.path.exists(self.filepath):
            return default
        with open(self.filepath, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def write(self, data: Any) -> None:
        os.makedirs(os.path.dirname(self.filepath) or '.', exist_ok=True)
        with open(self.filepath, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def append(self, item: Any, key: Optional[str] = None) -> None:
        data = self.read(default={})
        if key:
            data[key] = item
        else:
            if isinstance(data, list):
                data.append(item)
            else:
                data = [item]
        self.write(data)
