"""Строгое чтение AI-конфига и общий lock всех writers одного документа."""

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path

from src.ai_settings import AISettingsError
from src.utils.logger import warning


def read_config_document(path):
    """Не исправляет, не редактирует и не заменяет повреждённый файл defaults."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict):
            raise ValueError()
        return document
    except (OSError, ValueError, UnicodeError) as exc:
        warning(f"⚠️ Не удалось прочитать сохранённую конфигурацию AI: тип={type(exc).__name__}", category="config")
        raise AISettingsError("Не удалось прочитать сохранённую конфигурацию AI") from None


@contextmanager
def config_write_lock(path):
    # Отдельный inode lock: атомарный rename самого JSON не меняет блокировку.
    path = Path(path)
    with (path.parent / f".{path.name}.lock").open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
