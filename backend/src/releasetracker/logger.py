import logging
import sys

# Default log format
DEFAULT_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class LogConfig:
    """Centralized logging configuration"""

    @staticmethod
    def setup_logging(
        level: int = logging.INFO,
        format: str = DEFAULT_LOG_FORMAT,
        datefmt: str = DEFAULT_DATE_FORMAT,
    ):
        """Configure global logging"""
        # Configure the root logger
        logging.basicConfig(
            level=level,
            format=format,
            datefmt=datefmt,
            handlers=[logging.StreamHandler(sys.stdout)],
            force=True,  # Force override of existing configuration
        )

        # Tune third-party log levels to reduce noise
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Get a standard logger"""
        return logging.getLogger(name)
