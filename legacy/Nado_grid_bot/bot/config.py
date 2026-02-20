"""Configuration loading and validation for Nado Grid Bot."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class BotConfig:
    """Validated bot configuration."""

    # Network
    network: str

    # Product
    symbol: str
    product_id: int

    # Grid parameters
    price_reference: str
    grid_step_pct: float
    lower_bound_pct: float
    upper_bound_pct: float
    levels_down: int
    levels_up: int
    order_size_x18: int
    no_action_outside_range: bool

    # Orders
    order_type: str
    reduce_only_on_sells: bool
    spot_leverage: bool
    order_ttl_sec: int

    # Fills / WS
    ws_enabled: bool
    stream_type: str
    pause_on_is_taker: bool

    # Polling fallback
    polling_enabled: bool
    poll_interval_sec: int

    # Retries
    max_retries: int
    backoff_base_sec: int

    # Paths
    log_path: str
    state_path: str

    # Secrets (from .env)
    private_key: str
    subaccount_name: str


def _validate(cfg: BotConfig) -> None:
    """Raise ValueError on invalid config values."""
    if cfg.grid_step_pct <= 0:
        raise ValueError(f"grid_step_pct must be > 0, got {cfg.grid_step_pct}")
    if cfg.lower_bound_pct <= 0:
        raise ValueError(f"lower_bound_pct must be > 0, got {cfg.lower_bound_pct}")
    if cfg.upper_bound_pct <= 0:
        raise ValueError(f"upper_bound_pct must be > 0, got {cfg.upper_bound_pct}")
    if cfg.levels_down <= 0:
        raise ValueError(f"levels_down must be > 0, got {cfg.levels_down}")
    if cfg.levels_up <= 0:
        raise ValueError(f"levels_up must be > 0, got {cfg.levels_up}")
    if cfg.order_size_x18 <= 0:
        raise ValueError(f"order_size_x18 must be > 0, got {cfg.order_size_x18}")
    if cfg.product_id <= 0:
        raise ValueError(f"product_id must be > 0, got {cfg.product_id}")
    if cfg.order_ttl_sec <= 0:
        raise ValueError(f"order_ttl_sec must be > 0, got {cfg.order_ttl_sec}")
    if not cfg.private_key or cfg.private_key == "0x_YOUR_PRIVATE_KEY_HERE":
        raise ValueError("NADO_PRIVATE_KEY is not set or still placeholder")
    if cfg.network not in ("mainnet", "testnet", "devnet"):
        raise ValueError(f"Unknown network: {cfg.network}")

    # Warn if grid_step_pct is below commission breakeven (~0.02%)
    min_step = 0.020002
    if cfg.grid_step_pct < min_step:
        import warnings

        warnings.warn(
            f"grid_step_pct={cfg.grid_step_pct}% is below maker-maker breakeven "
            f"~{min_step}%. Round-trips will be unprofitable.",
            stacklevel=2,
        )


def load_config(
    config_path: str = "config.yaml",
    env_path: str = ".env",
) -> BotConfig:
    """Load configuration from YAML + .env files.

    Args:
        config_path: Path to YAML config relative to project root.
        env_path: Path to .env file relative to project root.

    Returns:
        Validated BotConfig instance.
    """
    project_root = Path(__file__).resolve().parent.parent

    # Load .env
    env_file = project_root / env_path
    load_dotenv(env_file)

    # Load YAML
    yaml_file = project_root / config_path
    if not yaml_file.exists():
        raise FileNotFoundError(f"Config file not found: {yaml_file}")

    with open(yaml_file, "r") as f:
        raw = yaml.safe_load(f)

    grid = raw.get("grid", {})
    orders = raw.get("orders", {})
    fills = raw.get("fills", {})
    polling = raw.get("polling_fallback", {})
    retries = raw.get("retries", {})
    paths = raw.get("paths", {})
    product = raw.get("product", {})

    cfg = BotConfig(
        network=raw.get("network", "mainnet"),
        symbol=product.get("symbol", "BTC-PERP"),
        product_id=product.get("product_id", 2),
        price_reference=grid.get("price_reference", "mark_price"),
        grid_step_pct=float(grid.get("grid_step_pct", 0.1)),
        lower_bound_pct=float(grid.get("lower_bound_pct", 5.0)),
        upper_bound_pct=float(grid.get("upper_bound_pct", 5.0)),
        levels_down=int(grid.get("levels_down", 20)),
        levels_up=int(grid.get("levels_up", 20)),
        order_size_x18=int(grid.get("order_size_x18", 10000000000000000)),
        no_action_outside_range=bool(grid.get("no_action_outside_range", True)),
        order_type=orders.get("order_type", "POST_ONLY"),
        reduce_only_on_sells=bool(orders.get("reduce_only_on_sells", True)),
        spot_leverage=bool(orders.get("spot_leverage", False)),
        order_ttl_sec=int(orders.get("order_ttl_sec", 86400)),
        ws_enabled=bool(fills.get("ws_enabled", True)),
        stream_type=fills.get("stream_type", "fill"),
        pause_on_is_taker=bool(fills.get("pause_on_is_taker", True)),
        polling_enabled=bool(polling.get("enabled", True)),
        poll_interval_sec=int(polling.get("poll_interval_sec", 5)),
        max_retries=int(retries.get("max_retries", 5)),
        backoff_base_sec=int(retries.get("backoff_base_sec", 1)),
        log_path=paths.get("log_path", "logs/bot.log"),
        state_path=paths.get("state_path", "data/state.sqlite"),
        private_key=os.environ.get("NADO_PRIVATE_KEY", ""),
        subaccount_name=os.environ.get("NADO_SUBACCOUNT_NAME", "default"),
    )

    _validate(cfg)
    return cfg
