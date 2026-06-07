"""
结构化日志模块
使用 loguru 进行统一日志管理
"""

import sys
from pathlib import Path
from datetime import datetime

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    print("loguru 未安装，使用标准 logging。建议: pip install loguru")


class StrategyLogger:
    """策略专用日志器"""

    def __init__(self, config):
        self.config = config
        self.log_dir = Path(config.output.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        log_level = config.output.get('log_level', 'INFO')

        # 配置 loguru
        try:
            logger.remove()  # 移除默认handler

            # 控制台输出
            logger.add(
                sys.stdout,
                format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
                level=log_level,
                colorize=True,
            )

            # 文件输出 (按天轮转)
            logger.add(
                self.log_dir / "strategy_{time:YYYY-MM-DD}.log",
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
                level="DEBUG",
                rotation="00:00",
                retention="30 days",
                encoding="utf-8",
            )

            # 信号专用文件
            logger.add(
                self.log_dir / "signals_{time:YYYY-MM-DD}.log",
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
                level="INFO",
                rotation="00:00",
                retention="90 days",
                encoding="utf-8",
                filter=lambda record: record["extra"].get("type") == "signal",
            )

        except Exception:
            pass

    def log_signal(self, timestamp: datetime, symbol: str, action: str, details: dict):
        """记录交易信号"""
        msg = f"SIGNAL | {action} | {symbol} | score={details.get('score', 0):.1f} | {details}"
        try:
            logger.bind(type="signal").info(msg)
        except Exception:
            logger.info(msg)

    def log_trade(self, trade):
        """记录交易执行"""
        direction = trade.direction.upper()
        pnl_str = f" PnL={trade.pnl:.2f}" if hasattr(trade, 'pnl') and trade.pnl else ""
        msg = f"TRADE | {direction} | {trade.symbol} {trade.name} | {trade.shares}股 @ {trade.price:.2f} | 金额={trade.amount:.2f}{pnl_str}"
        logger.info(msg)

    def log_market_snapshot(self, timestamp: datetime, market_data: dict):
        """记录市场快照"""
        msg = (f"MARKET | 涨跌比={market_data.get('ad_ratio', '?')} | "
               f"涨={market_data.get('advance', '?')} 跌={market_data.get('decline', '?')} | "
               f"情绪={market_data.get('sentiment', '?')}")
        logger.info(msg)

    def log_error(self, error: Exception, context: dict = None):
        """记录错误"""
        ctx = f" | context={context}" if context else ""
        logger.error(f"ERROR | {type(error).__name__}: {error}{ctx}")

    def log_info(self, message: str):
        """一般信息"""
        logger.info(message)

    def log_warning(self, message: str):
        """警告"""
        logger.warning(message)

    def log_debug(self, message: str):
        """调试信息"""
        logger.debug(message)
