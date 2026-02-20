"""Order execution layer for Nado Grid Bot.

Handles placement of initial grid, refill orders, throttling, error handling,
and reconciliation of local state vs exchange open orders.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from bot.config import BotConfig
from bot.exchange_client import (
    BookInfo,
    ExchangeClient,
    NadoApiError,
    ERR_AMOUNT_TOO_SMALL,
    ERR_INVALID_AMOUNT_INCREMENT,
    ERR_INVALID_PRICE_INCREMENT,
    ERR_POST_ONLY_CROSSES,
    ERR_RATE_LIMIT,
)
from bot.grid_engine import GridLevel, OrderIntent, on_buy_fill, on_sell_fill
from bot.state_store import (
    StateStore,
    ORDER_STATUS_ACTIVE,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_EXTERNAL,
    BOT_STATE_PAUSED,
)

logger = logging.getLogger("nado_grid")

# Throttle between consecutive place_order calls (seconds).
# "30 orders/min or 5 orders/10 sec" ⇒ ~200 ms is safe.
THROTTLE_INTERVAL_SEC = 0.25


class OrderExecutor:
    """Safely places and cancels grid orders with throttle and error handling."""

    def __init__(
        self,
        exchange_client: ExchangeClient,
        state_store: StateStore,
        config: BotConfig,
        book_info: BookInfo,
        levels: list[GridLevel],
        dry_run: bool = False,
    ) -> None:
        self._ec = exchange_client
        self._store = state_store
        self._config = config
        self._book_info = book_info
        self._levels = levels
        self._dry_run = dry_run

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def levels(self) -> list[GridLevel]:
        return self._levels

    @levels.setter
    def levels(self, value: list[GridLevel]) -> None:
        self._levels = value

    # ------------------------------------------------------------------
    # Place initial grid
    # ------------------------------------------------------------------

    def place_initial_grid(self, intents: list[OrderIntent]) -> int:
        """Place the startup order set.

        Returns the number of successfully placed orders.
        """
        logger.info("grid_initial_placing", extra={"total_orders": len(intents)})
        placed = 0
        failed = 0

        for intent in intents:
            # Invariant checks
            self._assert_invariants(intent)

            # Check no duplicate active order at this level/side
            existing = self._store.get_active_order_at_level(intent.k, intent.side)
            if existing is not None:
                logger.info(
                    "order_already_exists",
                    extra={"k": intent.k, "side": intent.side, "digest": existing.order_digest},
                )
                continue

            if self._dry_run:
                logger.info(
                    "dry_run_order",
                    extra={
                        "k": intent.k,
                        "side": intent.side,
                        "price_x18": str(intent.price_x18),
                        "amount": str(intent.amount),
                        "reduce_only": intent.reduce_only,
                    },
                )
                placed += 1
                continue

            ok = self._place_with_retry(intent)
            if ok:
                placed += 1
            else:
                failed += 1

            time.sleep(THROTTLE_INTERVAL_SEC)

        logger.info(
            "grid_initial_complete",
            extra={"placed": placed, "failed": failed},
        )
        return placed

    # ------------------------------------------------------------------
    # Refill: handle a fill event
    # ------------------------------------------------------------------

    def handle_fill(
        self,
        order_digest: str,
        filled_qty: int,
        remaining_qty: int,
        price: int,
        is_taker: bool,
        fee: int,
        is_bid: bool,
    ) -> None:
        """Process a fill event from the fills listener.

        1. Log fill to DB.
        2. Update order remaining.
        3. If order fully filled, compute and place refill.
        """
        # Log fill
        self._store.log_fill(
            order_digest=order_digest,
            filled_qty=filled_qty,
            remaining_qty=remaining_qty,
            price=price,
            is_taker=is_taker,
            fee=fee,
        )

        # CRITICAL: taker detection
        if is_taker:
            logger.critical(
                "taker_fill_detected",
                extra={
                    "order_digest": order_digest,
                    "filled_qty": str(filled_qty),
                    "price": str(price),
                    "fee": str(fee),
                },
            )
            self._store.set_bot_state(BOT_STATE_PAUSED)
            return

        # Lookup order in state
        order_row = self._store.get_order(order_digest)
        if order_row is None:
            logger.warning(
                "fill_for_unknown_order",
                extra={"order_digest": order_digest},
            )
            return

        # Update remaining in DB
        self._store.update_order_remaining(order_digest, remaining_qty)

        # If fully filled, mark order as filled
        if remaining_qty <= 0:
            self._store.update_order_status(order_digest, ORDER_STATUS_FILLED)

        # Check if bot is paused
        if self._store.get_bot_state() == BOT_STATE_PAUSED:
            logger.info("fill_while_paused", extra={"order_digest": order_digest})
            return

        # Determine refill intent
        k = order_row.k
        side = order_row.side

        if side == "buy":
            refill_side = "sell"
            pending = self._store.get_pending_buffer(k + 1, "sell")
            intent, leftover = on_buy_fill(
                k_buy=k,
                filled_qty=filled_qty,
                levels=self._levels,
                book_info=self._book_info,
                pending_buffer=pending,
            )
            self._store.set_pending_buffer(k + 1, "sell", leftover)
        elif side == "sell":
            refill_side = "buy"
            pending = self._store.get_pending_buffer(k - 1, "buy")
            intent, leftover = on_sell_fill(
                k_sell=k,
                filled_qty=filled_qty,
                levels=self._levels,
                book_info=self._book_info,
                pending_buffer=pending,
            )
            self._store.set_pending_buffer(k - 1, "buy", leftover)
        else:
            logger.warning("unknown_order_side", extra={"side": side})
            return

        if intent is None:
            logger.info(
                "refill_buffered",
                extra={"k": k, "side": side, "leftover": str(leftover)},
            )
            return

        # Check no duplicate at target level
        existing = self._store.get_active_order_at_level(intent.k, intent.side)
        if existing is not None:
            logger.info(
                "refill_level_already_active",
                extra={"k": intent.k, "side": intent.side, "digest": existing.order_digest},
            )
            return

        # Validate invariants
        self._assert_invariants(intent)

        if self._dry_run:
            logger.info(
                "dry_run_refill",
                extra={
                    "k": intent.k,
                    "side": intent.side,
                    "price_x18": str(intent.price_x18),
                    "amount": str(intent.amount),
                    "reduce_only": intent.reduce_only,
                },
            )
            return

        ok = self._place_with_retry(intent)
        if ok:
            refill_event = "refill_sell_placed" if intent.side == "sell" else "refill_buy_placed"
            logger.info(
                refill_event,
                extra={
                    "k_tp" if intent.side == "sell" else "k_rebuy": intent.k,
                    "price_x18": str(intent.price_x18),
                    "qty": str(abs(intent.amount)),
                    "reduce_only": intent.reduce_only,
                },
            )

    # ------------------------------------------------------------------
    # Reconcile local state vs exchange
    # ------------------------------------------------------------------

    def reconcile(self) -> dict:
        """Compare local state with exchange open orders.

        Returns dict with reconciliation summary.
        """
        exchange_orders = self._ec.get_open_orders(self._config.product_id)
        exchange_digests = {o.digest for o in exchange_orders}

        local_active = self._store.get_active_orders()
        local_digests = {o.order_digest for o in local_active}

        matched = exchange_digests & local_digests
        missing_from_exchange = local_digests - exchange_digests
        unknown_on_exchange = exchange_digests - local_digests

        # Mark missing orders (probably filled or cancelled externally)
        for digest in missing_from_exchange:
            self._store.update_order_status(digest, ORDER_STATUS_FILLED)

        # Mark unknown orders as external
        for digest in unknown_on_exchange:
            # Find matching exchange order for data
            for eo in exchange_orders:
                if eo.digest == digest:
                    self._store.upsert_order(
                        order_digest=eo.digest,
                        k=0,  # unknown level
                        side="buy" if int(eo.amount) > 0 else "sell",
                        price_x18=int(eo.price_x18),
                        qty_total_x18=abs(int(eo.amount)),
                        qty_remaining_x18=abs(int(eo.unfilled_amount)),
                        status=ORDER_STATUS_EXTERNAL,
                    )
                    break

        result = {
            "matched": len(matched),
            "missing_from_exchange": len(missing_from_exchange),
            "unknown_on_exchange": len(unknown_on_exchange),
        }

        logger.info(
            "reconcile_complete",
            extra=result,
        )

        if missing_from_exchange:
            logger.info(
                "missing_orders_marked_filled",
                extra={"count": len(missing_from_exchange)},
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_invariants(self, intent: OrderIntent) -> None:
        """Check critical invariants before placing an order."""
        tick = self._book_info.price_increment_x18
        size_inc = self._book_info.size_increment

        # Price must be tick-aligned
        assert intent.price_x18 % tick == 0, f"Price {intent.price_x18} not aligned to tick {tick}"

        # Size must be size_increment-aligned
        assert abs(intent.amount) % size_inc == 0, (
            f"Amount {intent.amount} not aligned to size_increment {size_inc}"
        )

        # Sell must be reduce_only
        if intent.side == "sell":
            assert intent.reduce_only, "Sell orders must be reduce_only"

        # Price within range
        # (relaxed: we trust grid_engine to generate valid levels)

    def _place_with_retry(self, intent: OrderIntent, max_retries: int = 5) -> bool:
        """Try to place an order with retry on rate limit.

        Returns True on success, False on permanent failure.
        """
        for attempt in range(max_retries + 1):
            try:
                digest, resp = self._ec.place_post_only_order(
                    product_id=self._config.product_id,
                    price_x18=intent.price_x18,
                    amount=intent.amount,
                    reduce_only=intent.reduce_only,
                )
                # Save to state
                self._store.upsert_order(
                    order_digest=digest,
                    k=intent.k,
                    side=intent.side,
                    price_x18=intent.price_x18,
                    qty_total_x18=abs(intent.amount),
                    qty_remaining_x18=abs(intent.amount),
                    status=ORDER_STATUS_ACTIVE,
                )
                return True

            except NadoApiError as e:
                if e.code == ERR_RATE_LIMIT and attempt < max_retries:
                    wait = self._config.backoff_base_sec * (2**attempt)
                    logger.warning(
                        "rate_limit_backoff",
                        extra={"err_code": e.code, "attempt": attempt, "wait_sec": wait},
                    )
                    time.sleep(wait)
                    continue

                if e.code == ERR_POST_ONLY_CROSSES:
                    logger.warning(
                        "post_only_crosses_book",
                        extra={
                            "err_code": e.code,
                            "k": intent.k,
                            "side": intent.side,
                            "price_x18": str(intent.price_x18),
                        },
                    )
                    return False

                if e.code in (
                    ERR_AMOUNT_TOO_SMALL,
                    ERR_INVALID_AMOUNT_INCREMENT,
                    ERR_INVALID_PRICE_INCREMENT,
                ):
                    logger.error(
                        "order_validation_error",
                        extra={
                            "err_code": e.code,
                            "message": e.message,
                            "k": intent.k,
                            "side": intent.side,
                        },
                    )
                    return False

                # Unknown error
                logger.error(
                    "order_placement_error",
                    extra={
                        "err_code": e.code,
                        "message": e.message,
                        "k": intent.k,
                        "side": intent.side,
                    },
                )
                return False

            except Exception as e:
                if attempt < max_retries:
                    wait = self._config.backoff_base_sec * (2**attempt)
                    logger.warning(
                        "network_error_retry",
                        extra={"error": str(e), "attempt": attempt, "wait_sec": wait},
                    )
                    time.sleep(wait)
                    continue
                logger.error(
                    "order_placement_failed",
                    extra={"error": str(e), "k": intent.k, "side": intent.side},
                )
                return False

        return False
