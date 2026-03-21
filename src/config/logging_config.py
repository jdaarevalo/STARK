"""
Logging configuration for S.T.A.R.K.
Call setup_logging() once from main.py before importing any other src module.
"""
import logging
import sys

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# All src.* loggers inherit from this one
PROJECT_LOGGER_NAME = "src"


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configures the logging system for S.T.A.R.K.

    Must be called ONCE at the start of main.py, before importing any src/ module.
    Idempotent: additional calls are no-ops.

    Args:
        level: logging level (e.g. logging.DEBUG, logging.INFO). Default: INFO.
    """
    logger = logging.getLogger(PROJECT_LOGGER_NAME)

    # Idempotency guard
    if logger.handlers:
        return

    logger.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATE_FORMAT)
    )
    logger.addHandler(console_handler)

    # Prevent messages from bubbling up to the root logger (e.g. chainlit, httpx handlers)
    logger.propagate = False

    # To add file logging in the future, uncomment and adapt:
    #
    # from logging.handlers import RotatingFileHandler
    # from pathlib import Path
    # log_dir = Path(__file__).resolve().parents[2] / "logs"
    # log_dir.mkdir(exist_ok=True)
    # file_handler = RotatingFileHandler(
    #     log_dir / "stark.log",
    #     maxBytes=5 * 1024 * 1024,  # 5 MB
    #     backupCount=3,
    #     encoding="utf-8",
    # )
    # file_handler.setLevel(logging.DEBUG)
    # file_handler.setFormatter(logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATE_FORMAT))
    # logger.addHandler(file_handler)
