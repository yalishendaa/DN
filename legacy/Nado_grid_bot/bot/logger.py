"""Structured JSON logging for Nado Grid Bot."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        # Merge extra fields passed via `extra={...}`
        for key in (
            "product_id",
            "order_digest",
            "k",
            "side",
            "price_x18",
            "qty_x18",
            "is_taker",
            "fee_x18",
            "err_code",
            "status",
            "mark_price_x18",
            "update_time",
            "price_increment_x18",
            "size_increment",
            "min_size",
            "mode",
            "digest",
            "total_orders",
            "placed",
            "failed",
            "expected",
            "actual",
            "diff",
            "count",
            "endpoint",
            "stream",
            "filled_qty",
            "remaining_qty",
            "is_bid",
            "fee",
            "k_tp",
            "k_rebuy",
            "qty",
            "reduce_only",
            "orders_in_state",
            "bot_state",
            "open_orders_exchange",
            "matched",
            "missing_from_exchange",
            "unknown_on_exchange",
            "grid_step_pct",
            "levels_down",
            "levels_up",
            "P0",
            "levels_count",
            "buy_levels",
            "sell_targets",
            "total_buy_orders",
            "total_notional_x18",
            "amount",
            "symbol",
            "elapsed_ms",
        ):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger(
    name: str = "nado_grid",
    level: int = logging.INFO,
    log_path: str | None = None,
) -> logging.Logger:
    """Create and configure the application logger.

    Args:
        name: Logger name.
        level: Minimum log level.
        log_path: Optional file path for log output.

    Returns:
        Configured Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = JsonFormatter()

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Optional file handler
    if log_path:
        import os

        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger
