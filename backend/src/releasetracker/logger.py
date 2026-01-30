import logging
import sys

# 默认日志格式
DEFAULT_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class LogConfig:
    """日志统一配置"""

    @staticmethod
    def setup_logging(
        level: int = logging.INFO,
        format: str = DEFAULT_LOG_FORMAT,
        datefmt: str = DEFAULT_DATE_FORMAT,
    ):
        """配置全局日志"""
        # 配置根日志记录器
        logging.basicConfig(
            level=level,
            format=format,
            datefmt=datefmt,
            handlers=[logging.StreamHandler(sys.stdout)],
            force=True,  # 强制覆盖现有配置
        )

        # 调整第三方库的日志级别，减少噪音
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """获取标准 logger"""
        return logging.getLogger(name)
