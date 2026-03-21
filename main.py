"""S.T.A.R.K. - Smart Training & Athletic Readiness Kernel"""
from src.config.logging_config import setup_logging

# Configure logging FIRST, before any other src/ import
setup_logging()

import logging

logger = logging.getLogger(__name__)


def main():
    logger.info("S.T.A.R.K. initialized.")


if __name__ == "__main__":
    main()
