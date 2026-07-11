import logging
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", default=False,
        help="Запустить тесты, обращающиеся к реальной бирже",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: тест обращается к реальной бирже")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live-тест отключён; используйте --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True, scope="session")
def isolate_test_logging():
    """Prevent tests from writing to production log files."""
    for name in ("steps", "trades"):
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.FileHandler)]
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
