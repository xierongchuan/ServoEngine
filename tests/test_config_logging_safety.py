"""Диагностика конфигурации: структурированные события без утечки секретов."""

import ast
from copy import deepcopy
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest


CONFIG_PATH = Path(__file__).resolve().parents[1] / "src" / "config.py"
SECRETS = {
    "OPENROUTER_API_KEY": "synthetic-openrouter-secret-2026",
    "DEEPSEEK_API_KEY": "synthetic-deepseek-secret-2026",
    "LM_STUDIO_API_KEY": "synthetic-lmstudio-secret-2026",
    "BINGX_API_KEY": "synthetic-bingx-key-2026",
    "BINGX_SECRET_KEY": "synthetic-bingx-secret-2026",
    "MEXC_API_KEY": "synthetic-mexc-key-2026",
    "MEXC_SECRET_KEY": "synthetic-mexc-secret-2026",
}
ERROR_SECRETS = ("synthetic-query-secret", "synthetic-token-secret", "synthetic-signature-secret")


@pytest.fixture
def config_logs(tmp_path, monkeypatch):
    from src import config_loader, trading_settings
    from src.utils import logger as logs
    from src.utils.log_store import EventReader

    data_dir = tmp_path / "data"
    monkeypatch.setenv("SERVO_DATA_DIR", str(data_dir))
    for name, secret in SECRETS.items():
        monkeypatch.setenv(name, secret)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "active.json").write_text("{}", encoding="utf-8")
    previous_dir = logs.LOG_DIR
    previous_level = logging.getLogger().level
    previous_context = logs.get_log_context()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logging.getLogger().addHandler(handler)
    logs.clear_log_context()
    # Чужая категория worker не должна перекрывать явную категорию диагностики.
    logs.set_log_context(category="market_data")

    def load(*, exchange="bingx", strategy_leverage=True, missing_ai_key=False):
        monkeypatch.setenv("OPENROUTER_API_KEY", "" if missing_ai_key else SECRETS["OPENROUTER_API_KEY"])
        logs.configure_logging(data_dir)
        settings = deepcopy(trading_settings.DEFAULT_TRADING_SETTINGS)
        settings["exchange"] = exchange
        monkeypatch.setattr(trading_settings, "get_trading_settings", lambda: settings)
        bot_config = {
            "STRATEGY_STYLE": "SCALP",
            "CHART_RANGES": {"1D": {"days": 1}, "6h": {"hours": 6}, "4h": {"hours": 4}},
            "DEFAULT_CHART_RANGE": "1D",
            "DEFAULT_PLOTTER_RANGE": "1D",
            "STYLE_PRESETS": {"SCALP": {
                "leverage": 15, "timeframe": "1m", "chart_period": "6h", "plotter_period": "4h",
            }},
            "SCALP_SETTINGS": {"preset": {"leverage": 7}} if strategy_leverage else {},
            # Произвольный секретный раздел нельзя выводить при загрузке или callback.
            "PRIVATE_CONFIG": {"api_key": SECRETS["BINGX_API_KEY"]},
        }
        monkeypatch.setattr(config_loader, "get_legacy_compatible_config", lambda: deepcopy(bot_config))
        module = ModuleType("isolated_config_logging")
        # Изолируем .env и пути конфигурации, не перезагружая рабочий src.config.
        module.__file__ = str(tmp_path / "src" / "config.py")
        exec(compile(CONFIG_PATH.read_text(encoding="utf-8"), str(CONFIG_PATH), "exec"), vars(module))
        return module

    reader = EventReader(data_dir)
    try:
        yield SimpleNamespace(
            load=load, events=lambda: reader.events(limit=200)["events"],
            data_dir=data_dir, stream=stream, loader=config_loader,
        )
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        logs.clear_log_context()
        logs.set_log_context(**previous_context)
        logs.configure_logging(previous_dir, level=previous_level)


def assert_safe_logs(config_logs, captured):
    events = config_logs.events()
    assert events
    assert all(event["category"] == "config" for event in events)
    assert all(re.search("[А-Яа-яЁё]", event["message"]) for event in events)
    content = json.dumps(events, ensure_ascii=False) + config_logs.stream.getvalue()
    content += captured.out + captured.err
    for path in config_logs.data_dir.rglob("*"):
        if path.is_file():
            content += path.read_bytes().decode("utf-8", errors="replace")
    for secret in (*SECRETS.values(), *ERROR_SECRETS):
        assert secret not in content
    assert captured.out == ""
    assert captured.err == ""
    return events


def test_cold_start_auto_configures_logging_after_loading_dotenv(tmp_path):
    secret = "synthetic-dotenv-only-secret-2026"
    (tmp_path / ".env").write_text(f"CONFIG_LOGGING_TEST_API_KEY={secret}\n", encoding="utf-8")
    (tmp_path / "bot_config.json").write_text(json.dumps({"AI_SETTINGS": {"model": secret}}))
    data_dir = tmp_path / "data"
    environment = {**os.environ, "SERVO_DATA_DIR": str(data_dir), "OPENROUTER_API_KEY": ""}
    environment.pop("CONFIG_LOGGING_TEST_API_KEY", None)
    script = """
import os
from pathlib import Path
import sys
from types import ModuleType

assert "src.utils.logger" not in sys.modules
assert not (Path(os.environ["SERVO_DATA_DIR"]) / "logs/events.sqlite3").exists()
module = ModuleType("isolated_config_logging")
module.__file__ = str(Path(sys.argv[2]) / "src/config.py")
source = Path(sys.argv[1])
exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), vars(module))
from src.utils.log_store import EventReader
events = EventReader(os.environ["SERVO_DATA_DIR"]).events(limit=200)["events"]
assert events and all(event["category"] == "config" for event in events)
assert any("Неверная конфигурация ИИ" in event["message"] for event in events)
assert "src.config" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(CONFIG_PATH), str(tmp_path)],
        cwd=CONFIG_PATH.parents[1], env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert (data_dir / "logs/events.sqlite3").is_file()
    for path in data_dir.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_all_config_diagnostics_use_explicit_category_and_no_print():
    tree = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert not any(node.func.id == "print" for node in calls)
    diagnostics = [node for node in calls if node.func.id in {"info", "warning", "error"}]
    assert diagnostics
    for node in diagnostics:
        assert any(
            keyword.arg == "category" and isinstance(keyword.value, ast.Constant) and keyword.value.value == "config"
            for keyword in node.keywords
        )


@pytest.mark.parametrize("exchange", ["bingx", "mexc"])
@pytest.mark.parametrize("strategy_leverage", [True, False])
@pytest.mark.parametrize("missing_ai_key", [True, False])
def test_startup_is_structured_without_stdout_or_secret_dump(
    config_logs, capsys, exchange, strategy_leverage, missing_ai_key,
):
    config = config_logs.load(
        exchange=exchange, strategy_leverage=strategy_leverage, missing_ai_key=missing_ai_key,
    )
    events = assert_safe_logs(config_logs, capsys.readouterr())
    assert len(events) == (9 if missing_ai_key else 8)
    assert len({event["event_id"] for event in events}) == len(events)
    assert all(event["level"] == ("WARNING" if "Отсутствует ключ API" in event["message"] else "INFO") for event in events)
    messages = "\n".join(event["message"] for event in events)
    assert ("MEXC" if exchange == "mexc" else "BingX") in messages
    assert ("из конфигурации стратегии" if strategy_leverage else "из STYLE_PRESETS") in messages
    assert config.LEVERAGE == (7 if strategy_leverage else 15)
    assert config.DEFAULT_CHART_RANGE == config.BOT_CONFIG["DEFAULT_CHART_RANGE"] == "6h"
    assert config.DEFAULT_PLOTTER_RANGE == config.BOT_CONFIG["DEFAULT_PLOTTER_RANGE"] == "4h"
    assert config.SMART_SAMPLING["history_step"] == 17
    assert config.AI_API_KEY == ("" if missing_ai_key else SECRETS["OPENROUTER_API_KEY"])
    assert config.BINGX_SECRET_KEY == SECRETS["BINGX_SECRET_KEY"]
    assert config.MEXC_SECRET_KEY == SECRETS["MEXC_SECRET_KEY"]
    mirrors = [json.loads(line) for path in config_logs.data_dir.glob("logs/events/*.jsonl") for line in path.read_text().splitlines()]
    assert [event["event_id"] for event in mirrors] == [event["event_id"] for event in events]
    assert len((config_logs.data_dir / "steps.log").read_text().splitlines()) == len(events)


@pytest.mark.parametrize("failure", ["modular", "legacy", "reload", "callback", "symbol"])
def test_diagnostic_errors_are_redacted_and_keep_fallback_behavior(config_logs, monkeypatch, capsys, failure):
    config = config_logs.load()
    old_config = config.BOT_CONFIG
    old_mtime = config._config_mtime
    count = len(config_logs.events())
    message = (
        f"raw={SECRETS['BINGX_SECRET_KEY']}; apiKey={ERROR_SECRETS[0]}&"
        f"token={ERROR_SECRETS[1]}&signature={ERROR_SECRETS[2]}"
    )

    def fail(*args):
        raise RuntimeError(message)

    expected_level = "WARNING"
    if failure == "modular":
        Path(config._CONFIG_PATH).write_text(json.dumps({"POSITION_SIZE_PERCENT": 12, "AI_THRESHOLDS": {"custom": 1}}))
        monkeypatch.setattr(config_logs.loader, "get_legacy_compatible_config", fail)
        result = config.load_bot_config()
        assert result["POSITION_SIZE_PERCENT"] == 12
        assert result["AI_THRESHOLDS"] == {"custom": 1}
        assert result["MIN_TRADE_AMOUNT_USDT"] == 10.0
    elif failure == "legacy":
        monkeypatch.setattr(config, "_use_new_config_system", lambda: False)
        Path(config._CONFIG_PATH).write_text("{}")
        monkeypatch.setattr(config.json, "load", fail)
        result = config.load_bot_config()
        assert result["POSITION_SIZE_PERCENT"] == 5.0
        assert result["EXCHANGE_SYMBOLS"] == {"bingx": ["BTC-USDT"]}
    elif failure == "reload":
        monkeypatch.setattr(config, "load_bot_config", fail)
        assert config.reload_bot_config() is old_config
        assert config._config_mtime == old_mtime
        expected_level = "ERROR"
    elif failure == "callback":
        updated = {**old_config, "POSITION_SIZE_PERCENT": 12}
        monkeypatch.setattr(config, "load_bot_config", lambda: updated)
        received = []
        config.register_config_callback(fail)
        config.register_config_callback(lambda old, new: received.append((old, new)))
        assert config.reload_bot_config() is updated
        assert received == [(old_config, updated)]
        assert config.POSITION_SIZE_PERCENT == 12
        assert config.EXECUTION_MODE == "read_only"
        expected_level = "ERROR"
    else:
        monkeypatch.setattr(config_logs.loader, "resolve_symbol_config", fail)
        assert config.get_symbol_config("BTC-USDT") is old_config

    events = assert_safe_logs(config_logs, capsys.readouterr())[count:]
    assert events[0]["level"] == expected_level
    assert "[REDACTED]" in events[0]["message"]
    assert len(events) == (2 if failure == "modular" else 1)


def test_legacy_success_and_missing_file_preserve_defaults(config_logs, monkeypatch, capsys):
    config = config_logs.load()
    monkeypatch.setattr(config, "_use_new_config_system", lambda: False)
    count = len(config_logs.events())
    missing = config.load_bot_config()
    assert missing["POSITION_SIZE_PERCENT"] == 5.0
    assert len(config_logs.events()) == count
    Path(config._CONFIG_PATH).write_text(json.dumps({"POSITION_SIZE_PERCENT": 9}))
    loaded = config.load_bot_config()
    assert loaded["POSITION_SIZE_PERCENT"] == 9
    assert loaded["MIN_TRADE_AMOUNT_USDT"] == missing["MIN_TRADE_AMOUNT_USDT"]
    events = assert_safe_logs(config_logs, capsys.readouterr())
    assert len(events) == count + 1
    assert events[-1]["level"] == "INFO"
    assert "Загружена конфигурация" in events[-1]["message"]
