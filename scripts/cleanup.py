"""Безопасная offline-очистка. Запускайте через scripts/clean_project.sh (Podman)."""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid


class CleanupError(RuntimeError):
    pass


CACHE_SUFFIXES = {".json", ".csv", ".png", ".jpg", ".jpeg", ".webp", ".lock", ".tmp"}
RESET_FILES = (
    "trade_history.json", "decision_journal.json", "backtest_result.json", "web_tokens.json",
    "runtime_command.json", "runtime_status.json", "symbol_runtime_command.json", "symbol_runtime_status.json",
)
DURABLE_PREFIXES = (
    "active_trades", "position_owners", "positions_cache", "managed_state", "grid_state",
    "pending_orders", "unknown_orders", "order_journal", "execution_journal", "spot_ledger",
)
DATABASE = re.compile(r"\.(?:db|sqlite|sqlite3)(?:$|[.-])", re.IGNORECASE)


def checked_path(root: Path, path: Path) -> Path:
    """Не следуем symlink даже внутри проекта: inode lock и внешние файлы неприкосновенны."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise CleanupError("Путь очистки находится вне проекта") from None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise CleanupError(f"Обнаружена символическая ссылка: {relative}")
    return path


def files_under(root: Path, directory: Path):
    checked_path(root, directory)
    if not directory.exists():
        return
    if not directory.is_dir():
        raise CleanupError(f"Ожидался каталог: {directory.relative_to(root)}")
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories + files:
            checked_path(root, Path(parent) / name)
        for name in files:
            path = Path(parent) / name
            try:
                regular = stat.S_ISREG(path.stat().st_mode)
            except FileNotFoundError:
                # Во время preview писатель мог завершить atomic replace временного файла.
                continue
            if not regular:
                raise CleanupError(f"Необычный тип файла: {path.relative_to(root)}")
            yield path


def protected(path: Path) -> bool:
    return bool(DATABASE.search(path.name)) or path.name.lstrip(".").startswith(DURABLE_PREFIXES)


def build_plan(root: Path, mode: str) -> list[Path]:
    selected = set()
    data = root / "data"
    trees = [root / "charts", data / "charts", data / "prices", data / "news", data / "market_data"]
    instances = checked_path(root, data / "instances")
    if instances.exists():
        for instance in instances.iterdir():
            checked_path(root, instance)
            if instance.is_dir():
                trees.append(instance / "prices")
                if mode == "reset":
                    for name in ("decision_journal.json", "decision_journal.json.lock"):
                        path = checked_path(root, instance / name)
                        if path.is_file():
                            selected.add(path)
    for tree in trees:
        selected.update(path for path in files_under(root, tree)
                        if path.suffix in CACHE_SUFFIXES and not protected(path))

    # Не обходится .cache: там могут лежать private probes и незавершённые journals.
    for base in (root / "src", root / "tests", root / "scripts"):
        if not checked_path(root, base).exists():
            continue
        for parent, directories, _ in os.walk(base, followlinks=False):
            directories[:] = [name for name in directories
                              if name not in {"node_modules", "dist"} and not (Path(parent) / name).is_symlink()]
            if "__pycache__" in directories:
                selected.update(files_under(root, Path(parent) / "__pycache__"))
                directories.remove("__pycache__")
    for directory in (root / ".pytest_cache", root / "src/telegram_panel/frontend/dist"):
        selected.update(files_under(root, directory))

    if mode == "reset":
        for name in RESET_FILES:
            for path in [data / name, data / (name + ".lock"), *data.glob(f".{name}.*.tmp")]:
                checked_path(root, path)
                if path.is_file():
                    selected.add(path)
        for name in ("steps.log", "trades.log", "cron.log"):
            for path in data.glob(name + "*"):
                checked_path(root, path)
                if path.is_file():
                    selected.add(path)
        log_database = data / "logs/events.sqlite3"
        for path in files_under(root, data / "logs"):
            # Только известная логовая БД с её sidecars; схема проверяется перед применением.
            if not protected(path) or path in {log_database, *[Path(str(log_database) + suffix)
                                                            for suffix in ("-wal", "-shm", "-journal")] }:
                selected.add(path)
    return sorted(path for path in selected if not protected(path) or (
        mode == "reset" and path.parent == data / "logs" and path.name in {
            "events.sqlite3", "events.sqlite3-wal", "events.sqlite3-shm", "events.sqlite3-journal"}))


def assert_no_writers(root: Path, inventory, host_root: Path) -> None:
    if not isinstance(inventory, list):
        raise CleanupError("Не получен список контейнеров Podman")
    for container in inventory:
        if not isinstance(container, dict) or not isinstance(container.get("State"), dict):
            raise CleanupError("Некорректный результат podman inspect")
        state = container["State"]
        if not (state.get("Running") or state.get("Paused") or state.get("Restarting")
                or state.get("Status") in {"running", "paused", "restarting"}):
            continue
        mounts = container.get("Mounts")
        if not isinstance(mounts, list):
            raise CleanupError("Не удалось проверить mounts работающего контейнера")
        for mount in mounts:
            source = mount.get("Source") if isinstance(mount, dict) else None
            if not isinstance(source, str):
                raise CleanupError("Не удалось проверить источник mount")
            source = Path(source)
            if not source.is_absolute():
                raise CleanupError("Источник mount не является абсолютным путём")
            if source == host_root or source.is_relative_to(host_root) or host_root.is_relative_to(source):
                raise CleanupError("Остановите bot, panel и все контейнеры с mounts этого проекта")
    for name in ("runtime_status.json", "symbol_runtime_status.json"):
        path = checked_path(root, root / "data" / name)
        if not path.exists():
            continue
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(status, dict):
                raise ValueError()
            if name == "runtime_status.json" and status.get("state") not in {
                "stopped", "starting", "running", "stopping", "crashed"}:
                raise ValueError()
            workers = status.get("workers", [])
            if not isinstance(workers, list) or any(not isinstance(worker, dict) for worker in workers):
                raise ValueError()
            updated = status.get("updated_at_ts")
            if updated is not None and (type(updated) not in (int, float) or not math.isfinite(updated)):
                raise ValueError()
            fresh = updated is not None and time.time() - updated < 90
            busy = status.get("state") in {"running", "starting", "stopping"} or any(
                worker.get("alive") for worker in workers)
            if busy and updated is None:
                raise CleanupError("Статус runtime не позволяет подтвердить остановку")
            if fresh and busy:
                raise CleanupError("Сохранён свежий статус работающего runtime; остановите писателей")
        except (OSError, ValueError, TypeError):
            raise CleanupError(f"Не удалось проверить {name}; очистка запрещена") from None


def assert_reset_safe(root: Path) -> None:
    # Guard может логировать отказ: его вспомогательные логи направляются только в /tmp.
    with tempfile.TemporaryDirectory(prefix="servo-cleanup-check-") as temporary:
        environment = dict(os.environ, SERVO_DATA_DIR=temporary,
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        try:
            # Отдельный процесс не меняет logging handlers вызывающего приложения.
            result = subprocess.run([
                sys.executable, "-c",
                "import json,sys; from src.trading_settings import managed_state_block_reason; "
                "print(json.dumps(managed_state_block_reason(sys.argv[1]), ensure_ascii=False))",
                str(root / "data"),
            ], cwd=root, env=environment, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise CleanupError("Не удалось проверить торговое состояние; сброс запрещён")
            reason = json.loads(result.stdout)
            from src.trading_settings import _INFRA_SCHEMAS
            if reason:
                raise CleanupError("Сброс запрещён: " + reason)
            database = checked_path(root, root / "data/logs/events.sqlite3")
            if database.exists():
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    version, schema = _INFRA_SCHEMAS[0]
                    valid = tables - {"sqlite_sequence"} == schema.keys()
                    valid = valid and connection.execute("PRAGMA user_version").fetchone()[0] == version
                    for table, columns in schema.items():
                        valid = valid and {row[1] for row in connection.execute(f"PRAGMA table_info({table})")} == columns
                    if not valid:
                        raise CleanupError("events.sqlite3 не является известной логовой БД; она сохраняется")
        except subprocess.TimeoutExpired:
            raise CleanupError("Истёк срок проверки торгового состояния; сброс запрещён") from None


@contextmanager
def maintenance_lock(root: Path):
    directory = checked_path(root, root / ".cache")
    directory.mkdir(exist_ok=True)
    path = checked_path(root, directory / "project-cleanup.lock")
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CleanupError("Уже выполняется другая очистка") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_manifest(backup: Path, manifest: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=backup)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, backup / "manifest.json")
        descriptor = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_plan(root: Path, mode: str, plan: list[Path]) -> Path | None:
    backup = None
    manifest = {"mode": mode, "complete": False, "files": [str(path.relative_to(root)) for path in plan], "moved": []}
    if mode == "reset" and plan:
        parent = checked_path(root, root / ".cache/cleanup-backups")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = parent / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12])
        backup.mkdir(mode=0o700)
        write_manifest(backup, manifest)
    # Проверяем весь план до первой мутации, включая SQLite sidecars и специальные файлы.
    revisions = {path: checked_path(root, path).stat() for path in plan}
    try:
        for path in plan:
            current = checked_path(root, path).stat()
            if not stat.S_ISREG(current.st_mode) or current != revisions[path]:
                raise CleanupError("Файлы изменились после проверки; не запускайте писателей параллельно")
            if backup:
                destination = backup / path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Только rename: EXDEV не превращается в рискованное copy/delete SQLite.
                path.rename(destination)
                manifest["moved"].append(str(path.relative_to(root)))
            else:
                path.unlink()
        manifest["complete"] = True
    finally:
        if backup:
            write_manifest(backup, manifest)
            print(f"Архив: {backup.relative_to(root)}; complete={manifest['complete']}")
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Безопасная очистка: по умолчанию только просмотр, без изменений.")
    parser.add_argument("--mode", choices=("cache", "reset"), default="cache", help="cache: восстановимые данные; reset: также история/логи с архивом")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="Применить после остановки всех писателей")
    actions.add_argument("--dry-run", action="store_true", help="Предварительный просмотр (по умолчанию)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    parser.add_argument("--host-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--inventory-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.absolute()
    try:
        if root.is_symlink() or not (root / "Dockerfile").is_file() or not (root / "src").is_dir():
            raise CleanupError("Не найден корень проекта")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        plan = build_plan(root, args.mode)
        sizes = {}
        for path in plan:
            try:
                sizes[path] = path.stat().st_size
            except FileNotFoundError:
                continue
        plan = list(sizes)
        print(f"Режим: {args.mode}; файлов: {len(plan)}; объём: {sum(sizes.values())} байт")
        for path in plan[:25]:
            print("  " + str(path.relative_to(root)))
        if len(plan) > 25:
            print(f"  ... ещё {len(plan) - 25} файлов")
        print("Сохраняются: .env, config, durable trading state, rate limiter и неизвестные данные.")
        if not args.apply:
            print("Предварительный просмотр. Для применения: scripts/clean_project.sh --mode " + args.mode + " --apply")
            return 0
        if not args.inventory_stdin or args.host_root is None:
            raise CleanupError("Применение разрешено только через scripts/clean_project.sh с проверкой Podman")
        inventory = json.load(sys.stdin)
        assert_no_writers(root, inventory, args.host_root)
        with maintenance_lock(root):
            assert_no_writers(root, inventory, args.host_root)
            if args.mode == "reset":
                assert_reset_safe(root)
            # Guard читает committed WAL; учитываем все sidecars после его проверки.
            plan = build_plan(root, args.mode)
            apply_plan(root, args.mode, plan)
        print("✅ Очистка завершена. Перед запуском проверьте execution в config/trading.json; режим не изменён.")
        return 0
    except (CleanupError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"❌ Очистка остановлена: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
