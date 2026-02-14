import logging
import pytest


@pytest.fixture(autouse=True, scope="session")
def isolate_test_logging():
    """Prevent tests from writing to production log files."""
    for name in ("steps", "trades"):
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.FileHandler)]
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
