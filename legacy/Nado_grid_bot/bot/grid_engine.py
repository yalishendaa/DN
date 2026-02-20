"""Grid level generation and refill logic for Nado Grid Bot.

Pure functions — no SDK calls, no side effects.
All prices/amounts in x18 integer format.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from nado_protocol.utils.math import round_x18

from bot.exchange_client import BookInfo, round_up_x18

logger = logging.getLogger("nado_grid")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GridLevel:
    """A single level on the grid."""

    k: int  # level index (-levels_down .. +levels_up), 0 = P0
    price_x18: int  # rounded price in x18
    side: str  # "buy" or "sell_target"


@dataclass
class OrderIntent:
    """Instruction to place one order."""

    k: int
    side: str  # "buy" or "sell"
    price_x18: int
    amount: int  # signed: +buy, -sell
    reduce_only: bool


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------


def generate_grid_levels(
    p0: int,
    grid_step_pct: float,
    lower_bound_pct: float,
    upper_bound_pct: float,
    levels_down: int,
    levels_up: int,
    book_info: BookInfo,
) -> list[GridLevel]:
    """Generate deterministic grid levels centred on *p0*.

    Returns a list of GridLevel objects sorted by k (ascending).
    Prices are rounded:
      - buy levels: round DOWN to price_increment
      - sell targets: round UP to price_increment
    Levels outside [P_low, P_high] are filtered out.
    """
    tick = book_info.price_increment_x18
    assert tick > 0, f"price_increment_x18 must be > 0, got {tick}"

    p_low = int(p0 * (1 - lower_bound_pct / 100))
    p_high = int(p0 * (1 + upper_bound_pct / 100))

    levels: list[GridLevel] = []

    for k in range(-levels_down, levels_up + 1):
        raw_price = int(p0 * (1 + k * grid_step_pct / 100))

        if k < 0:
            # Buy level — round down
            price = round_x18(raw_price, tick)
            side = "buy"
        else:
            # k == 0 is P0 (sell target for k=-1 fills), k > 0 are regular sell targets
            price = round_up_x18(raw_price, tick)
            side = "sell_target"

        if price < p_low or price > p_high:
            continue
        if price <= 0:
            continue

        levels.append(GridLevel(k=k, price_x18=price, side=side))

    levels.sort(key=lambda lv: lv.k)

    logger.info(
        "grid_levels_generated",
        extra={
            "P0": str(p0),
            "levels_count": len(levels),
            "buy_levels": sum(1 for lv in levels if lv.side == "buy"),
            "sell_targets": sum(1 for lv in levels if lv.side == "sell_target"),
        },
    )
    return levels


# ---------------------------------------------------------------------------
# Initial order plan
# ---------------------------------------------------------------------------


def build_initial_orders(
    levels: list[GridLevel],
    order_size_x18: int,
    position_amount_x18: int,
    book_info: BookInfo,
) -> list[OrderIntent]:
    """Build the list of orders to place at startup.

    Args:
        levels: Full grid levels (from generate_grid_levels).
        order_size_x18: Fixed lot size in x18 (positive integer, base qty).
        position_amount_x18: Current perp position amount (x18). 0 = no position.
        book_info: Market spec for size validation.

    Returns:
        List of OrderIntent objects.
    """
    orders: list[OrderIntent] = []

    # -- Buy ladder (k < 0) -------------------------------------------------
    for lv in levels:
        if lv.k < 0 and lv.side == "buy":
            orders.append(
                OrderIntent(
                    k=lv.k,
                    side="buy",
                    price_x18=lv.price_x18,
                    amount=order_size_x18,  # positive = long/buy
                    reduce_only=False,
                )
            )

    # -- Sell orders for existing long position (k > 0) ----------------------
    if position_amount_x18 > 0:
        remaining = position_amount_x18
        sell_levels = sorted(
            [lv for lv in levels if lv.k > 0 and lv.side == "sell_target"],
            key=lambda lv: lv.k,
        )
        for lv in sell_levels:
            if remaining <= 0:
                break
            lot = min(order_size_x18, remaining)
            # Validate against size_increment
            lot = (lot // book_info.size_increment) * book_info.size_increment
            if lot <= 0:
                break
            orders.append(
                OrderIntent(
                    k=lv.k,
                    side="sell",
                    price_x18=lv.price_x18,
                    amount=-lot,  # negative = short/sell
                    reduce_only=True,
                )
            )
            remaining -= lot

    return orders


# ---------------------------------------------------------------------------
# Refill logic (called on each fill event)
# ---------------------------------------------------------------------------


def on_buy_fill(
    k_buy: int,
    filled_qty: int,
    levels: list[GridLevel],
    book_info: BookInfo,
    pending_buffer: int = 0,
) -> tuple[Optional[OrderIntent], int]:
    """Determine the sell order to place after a buy fill.

    Args:
        k_buy: Level index of the filled buy.
        filled_qty: Positive quantity filled (x18).
        levels: Grid levels.
        book_info: Market spec.
        pending_buffer: Previously accumulated qty that couldn't be placed.

    Returns:
        (OrderIntent or None, remaining_buffer).
    """
    k_tp = k_buy + 1
    tp_level = next((lv for lv in levels if lv.k == k_tp), None)
    if tp_level is None:
        logger.warning("refill_no_sell_level", extra={"k_buy": k_buy, "k_tp": k_tp})
        return None, pending_buffer + filled_qty

    total = pending_buffer + filled_qty
    placeable = (total // book_info.size_increment) * book_info.size_increment

    if placeable <= 0:
        return None, total

    intent = OrderIntent(
        k=k_tp,
        side="sell",
        price_x18=tp_level.price_x18,
        amount=-placeable,
        reduce_only=True,
    )
    leftover = total - placeable
    return intent, leftover


def on_sell_fill(
    k_sell: int,
    filled_qty: int,
    levels: list[GridLevel],
    book_info: BookInfo,
    pending_buffer: int = 0,
) -> tuple[Optional[OrderIntent], int]:
    """Determine the buy order to place after a sell fill.

    Args:
        k_sell: Level index of the filled sell.
        filled_qty: Positive quantity filled (x18, absolute).
        levels: Grid levels.
        book_info: Market spec.
        pending_buffer: Previously accumulated qty that couldn't be placed.

    Returns:
        (OrderIntent or None, remaining_buffer).
    """
    k_rebuy = k_sell - 1
    rebuy_level = next((lv for lv in levels if lv.k == k_rebuy), None)
    if rebuy_level is None:
        logger.warning("refill_no_buy_level", extra={"k_sell": k_sell, "k_rebuy": k_rebuy})
        return None, pending_buffer + filled_qty

    total = pending_buffer + filled_qty
    placeable = (total // book_info.size_increment) * book_info.size_increment

    if placeable <= 0:
        return None, total

    intent = OrderIntent(
        k=k_rebuy,
        side="buy",
        price_x18=rebuy_level.price_x18,
        amount=placeable,
        reduce_only=False,
    )
    leftover = total - placeable
    return intent, leftover
