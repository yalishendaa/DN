"""Main orchestration module for Nado Grid Bot.

Handles full lifecycle: init → reconcile → place grid → listen fills → shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from bot.config import BotConfig, load_config
from bot.exchange_client import BookInfo, ExchangeClient
from bot.execution import OrderExecutor
from bot.fills_listener import FillEvent, FillsListener, PollingFillsListener
from bot.grid_engine import (
    build_initial_orders,
    generate_grid_levels,
    GridLevel,
)
from bot.logger import setup_logger
from bot.state_store import (
    BOT_STATE_PAUSED,
    BOT_STATE_RUNNING,
    StateStore,
)

logger = logging.getLogger("nado_grid")


class GridBot:
    """Top-level controller that wires all components together."""

    def __init__(self, config: BotConfig, dry_run: bool = False) -> None:
        self._config = config
        self._dry_run = dry_run

        # Components (initialised in start())
        self._ec: Optional[ExchangeClient] = None
        self._store: Optional[StateStore] = None
        self._executor: Optional[OrderExecutor] = None
        self._ws_listener: Optional[FillsListener] = None
        self._poll_listener: Optional[PollingFillsListener] = None
        self._book_info: Optional[BookInfo] = None
        self._levels: list[GridLevel] = []
        self._p0: int = 0

        # Shutdown coordination
        self._shutdown_event = asyncio.Event()
        self._poll_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Full start: init components, reconcile, place grid, listen."""
        setup_logger(
            level=logging.DEBUG if os.environ.get("DEBUG") else logging.INFO,
            log_path=self._config.log_path,
        )
        logger.info("bot_starting", extra={"dry_run": self._dry_run})

        self._init_components()
        self._load_or_create_grid()
        self._reconcile_state()
        self._place_missing_orders()

        if self._dry_run:
            logger.info("dry_run_complete")
            self._store.close()
            return

        # Register signal handlers
        self._register_signals()

        # Run event loop with WS listener (and optionally polling)
        try:
            asyncio.run(self._run_listeners())
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def stop(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown_event.set()

    def pause(self) -> None:
        """Pause order placement (WS still listens)."""
        if self._store:
            self._store.set_bot_state(BOT_STATE_PAUSED)
            logger.info("bot_paused")

    def resume(self) -> None:
        """Resume order placement."""
        if self._store:
            self._store.set_bot_state(BOT_STATE_RUNNING)
            logger.info("bot_resumed")

    def status(self) -> dict:
        """Return current bot status."""
        self._init_components()

        bot_state = self._store.get_bot_state()
        grid_params = self._store.get_grid_params()
        active_orders = self._store.get_active_orders()
        pending_buffers = self._store.get_all_pending_buffers()

        try:
            mark_price = self._ec.get_mark_price(self._config.product_id)
        except Exception:
            mark_price = 0

        try:
            position = self._ec.get_perp_position_amount(self._config.product_id)
        except Exception:
            position = 0

        info = {
            "bot_state": bot_state,
            "p0": grid_params.get("p0_x18", "0") if grid_params else "0",
            "mark_price_x18": str(mark_price),
            "position_amount_x18": str(position),
            "active_orders": len(active_orders),
            "pending_buffers": len(pending_buffers),
            "buy_orders": sum(1 for o in active_orders if o.side == "buy"),
            "sell_orders": sum(1 for o in active_orders if o.side == "sell"),
        }

        self._store.close()
        return info

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _init_components(self) -> None:
        """Initialize SDK client, state store, book info."""
        if self._ec is None:
            self._ec = ExchangeClient(self._config)

        if self._store is None:
            project_root = Path(__file__).resolve().parent.parent
            db_path = project_root / self._config.state_path
            self._store = StateStore(str(db_path))

        if self._book_info is None:
            self._book_info = self._ec.get_book_info(self._config.product_id)

    def _load_or_create_grid(self) -> None:
        """Load existing grid params or create new ones from mark price."""
        existing = self._store.get_grid_params()

        if existing is not None:
            self._p0 = int(existing["p0_x18"])
            logger.info(
                "state_loaded",
                extra={
                    "orders_in_state": self._store.count_active_orders(),
                    "bot_state": self._store.get_bot_state(),
                    "P0": str(self._p0),
                },
            )
        else:
            # First run: get P0 from mark price
            self._p0 = self._ec.get_mark_price(self._config.product_id)
            self._store.save_grid_params(
                p0_x18=self._p0,
                grid_step_pct=self._config.grid_step_pct,
                levels_down=self._config.levels_down,
                levels_up=self._config.levels_up,
            )
            logger.info("grid_params_created", extra={"P0": str(self._p0)})

        # Generate grid levels
        self._levels = generate_grid_levels(
            p0=self._p0,
            grid_step_pct=self._config.grid_step_pct,
            lower_bound_pct=self._config.lower_bound_pct,
            upper_bound_pct=self._config.upper_bound_pct,
            levels_down=self._config.levels_down,
            levels_up=self._config.levels_up,
            book_info=self._book_info,
        )

        # Init executor
        self._executor = OrderExecutor(
            exchange_client=self._ec,
            state_store=self._store,
            config=self._config,
            book_info=self._book_info,
            levels=self._levels,
            dry_run=self._dry_run,
        )

    def _reconcile_state(self) -> None:
        """Reconcile local DB with exchange open orders."""
        if self._dry_run:
            return

        active_count = self._store.count_active_orders()
        if active_count == 0:
            logger.info("reconcile_skipped_no_state")
            return

        exchange_orders = self._ec.get_open_orders(self._config.product_id)
        logger.info(
            "reconcile_start",
            extra={
                "open_orders_exchange": len(exchange_orders),
                "orders_in_state": active_count,
            },
        )
        self._executor.reconcile()

    def _place_missing_orders(self) -> None:
        """Place initial grid orders that are not already active."""
        position = 0
        if not self._dry_run:
            try:
                position = self._ec.get_perp_position_amount(self._config.product_id)
            except Exception:
                position = 0

        intents = build_initial_orders(
            levels=self._levels,
            order_size_x18=self._config.order_size_x18,
            position_amount_x18=position,
            book_info=self._book_info,
        )

        if self._dry_run:
            self._executor.place_initial_grid(intents)
            # Print summary
            total_notional = sum(
                abs(i.amount) * i.price_x18 // (10**18) for i in intents if i.side == "buy"
            )
            logger.info(
                "dry_run_summary",
                extra={
                    "total_buy_orders": sum(1 for i in intents if i.side == "buy"),
                    "total_sell_orders": sum(1 for i in intents if i.side == "sell"),
                    "total_notional_x18": str(total_notional),
                },
            )
            return

        # Filter out intents for levels that already have active orders
        filtered = []
        for intent in intents:
            existing = self._store.get_active_order_at_level(intent.k, intent.side)
            if existing is None:
                filtered.append(intent)

        if filtered:
            self._executor.place_initial_grid(filtered)

        # Final reconcile after placement
        self._executor.reconcile()

    # ------------------------------------------------------------------
    # Fills handling
    # ------------------------------------------------------------------

    def _on_fill(self, event: FillEvent) -> None:
        """Callback from fills listener — routes to executor."""
        if self._executor is None:
            return
        self._executor.handle_fill(
            order_digest=event.order_digest,
            filled_qty=event.filled_qty,
            remaining_qty=event.remaining_qty,
            price=event.price,
            is_taker=event.is_taker,
            fee=event.fee,
            is_bid=event.is_bid,
        )

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    async def _run_listeners(self) -> None:
        """Run WS and/or polling listeners until shutdown."""
        tasks = []

        if self._config.ws_enabled:
            self._ws_listener = FillsListener(
                network=self._config.network,
                product_id=self._config.product_id,
                subaccount_hex=self._ec.sender_hex,
                callback=self._on_fill,
            )
            tasks.append(asyncio.create_task(self._ws_listener.run()))

        if self._config.polling_enabled:
            self._poll_listener = PollingFillsListener(
                exchange_client=self._ec,
                product_id=self._config.product_id,
                poll_interval_sec=self._config.poll_interval_sec,
                callback=self._on_fill,
            )
            self._poll_thread = threading.Thread(target=self._poll_listener.run_sync, daemon=True)
            self._poll_thread.start()

        # Wait for shutdown signal
        tasks.append(asyncio.create_task(self._wait_shutdown()))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_shutdown(self) -> None:
        """Block until shutdown event is set."""
        while not self._shutdown_event.is_set():
            await asyncio.sleep(1)

        # Stop listeners
        if self._ws_listener:
            await self._ws_listener.stop()
        if self._poll_listener:
            self._poll_listener.stop()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _register_signals(self) -> None:
        """Register SIGINT/SIGTERM handlers for graceful shutdown."""

        def _handler(signum, frame):
            logger.info("shutdown_signal_received", extra={"signal": signum})
            self.stop()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def _shutdown(self) -> None:
        """Clean up resources. Do NOT cancel orders automatically."""
        logger.info("bot_shutting_down")

        if self._poll_listener:
            self._poll_listener.stop()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)

        if self._store:
            self._store.close()

        logger.info("bot_stopped")
