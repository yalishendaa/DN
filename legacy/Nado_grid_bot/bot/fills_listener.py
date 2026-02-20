"""WebSocket fills listener and fallback polling for Nado Grid Bot.

Subscribes to the ``fill`` stream via Nado Subscriptions WebSocket.
Falls back to periodic polling of open orders when WS is disabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("nado_grid")

# ---------------------------------------------------------------------------
# WS endpoint derivation
# ---------------------------------------------------------------------------

# The gateway HTTP URL from SDK is like https://gateway.prod.nado.xyz/v1
# The WS endpoint is derived by replacing scheme and appending /ws.
_WS_ENDPOINT_MAP = {
    "mainnet": "wss://gateway.prod.nado.xyz/v1/ws",
    "testnet": "wss://gateway.test.nado.xyz/v1/ws",
    "devnet": "ws://localhost:80/ws",
}

# Maximum WS connection lifetime (rotate before 12h limit)
WS_MAX_LIFETIME_SEC = 11 * 3600  # 11 hours
# Ping interval (Nado requires < 30s)
WS_PING_INTERVAL_SEC = 25
# Reconnect backoff: 1, 2, 4, … up to 60s
WS_RECONNECT_MAX_SEC = 60


# ---------------------------------------------------------------------------
# Fill event dataclass
# ---------------------------------------------------------------------------


class FillEvent:
    """Parsed fill event from WebSocket or polling."""

    __slots__ = (
        "order_digest",
        "filled_qty",
        "remaining_qty",
        "original_qty",
        "price",
        "is_taker",
        "fee",
        "is_bid",
        "timestamp",
    )

    def __init__(
        self,
        order_digest: str,
        filled_qty: int,
        remaining_qty: int,
        original_qty: int,
        price: int,
        is_taker: bool,
        fee: int,
        is_bid: bool,
        timestamp: float = 0.0,
    ):
        self.order_digest = order_digest
        self.filled_qty = filled_qty
        self.remaining_qty = remaining_qty
        self.original_qty = original_qty
        self.price = price
        self.is_taker = is_taker
        self.fee = fee
        self.is_bid = is_bid
        self.timestamp = timestamp or time.time()


# Type alias for the callback the caller registers.
FillCallback = Callable[[FillEvent], None]


# ---------------------------------------------------------------------------
# WebSocket FillsListener (async)
# ---------------------------------------------------------------------------


class FillsListener:
    """Async WebSocket listener for fill events.

    Usage (from an asyncio event loop)::

        listener = FillsListener(network, product_id, subaccount_hex, on_fill)
        await listener.run()         # blocks, reconnects automatically
        await listener.stop()        # request graceful shutdown
    """

    def __init__(
        self,
        network: str,
        product_id: int,
        subaccount_hex: str,
        callback: FillCallback,
    ) -> None:
        self._network = network
        self._product_id = product_id
        self._subaccount = subaccount_hex
        self._callback = callback
        self._running = False
        self._ws = None
        self._endpoint = _WS_ENDPOINT_MAP.get(network, _WS_ENDPOINT_MAP["mainnet"])

    async def run(self) -> None:
        """Connect, subscribe, and listen for fills. Reconnects on failures."""
        self._running = True
        backoff = 1

        while self._running:
            try:
                await self._connect_and_listen()
                backoff = 1  # reset on clean cycle (lifetime rotation)
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    "ws_connection_error",
                    extra={"error": str(e), "backoff_sec": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, WS_RECONNECT_MAX_SEC)

    async def stop(self) -> None:
        """Signal the listener to shut down."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------

    async def _connect_and_listen(self) -> None:
        import websockets
        from websockets.extensions.permessage_deflate import ClientPerMessageDeflateFactory

        logger.info("ws_connecting", extra={"endpoint": self._endpoint})

        extensions = [ClientPerMessageDeflateFactory()]

        async with websockets.connect(
            self._endpoint,
            extensions=extensions,
            ping_interval=WS_PING_INTERVAL_SEC,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            logger.info("ws_connected", extra={"endpoint": self._endpoint})

            # Subscribe to fill stream
            sub_msg = {
                "method": "subscribe",
                "stream": {
                    "type": "fill",
                    "product_id": self._product_id,
                    "subaccount": self._subaccount,
                },
                "id": 1,
            }
            await ws.send(json.dumps(sub_msg))
            logger.info(
                "ws_subscribed",
                extra={"stream": "fill", "product_id": self._product_id},
            )

            connect_time = time.monotonic()

            async for raw_msg in ws:
                if not self._running:
                    break

                # Rotate connection before 12h limit
                elapsed = time.monotonic() - connect_time
                if elapsed > WS_MAX_LIFETIME_SEC:
                    logger.info("ws_lifetime_rotation", extra={"elapsed_sec": int(elapsed)})
                    break

                try:
                    self._handle_message(raw_msg)
                except Exception as e:
                    logger.error("ws_message_error", extra={"error": str(e)})

    def _handle_message(self, raw: str | bytes) -> None:
        data = json.loads(raw)

        # Subscription confirmation
        if data.get("method") == "subscribe" or data.get("type") == "subscription_ack":
            return

        # Ping/pong frames are handled by websockets library automatically
        # Check for fill data
        stream_data = data.get("data")
        if stream_data is None:
            # Could be heartbeat or other non-fill message
            return

        # Parse fill event
        try:
            event = FillEvent(
                order_digest=stream_data.get("order_digest", ""),
                filled_qty=int(stream_data.get("filled_qty", "0")),
                remaining_qty=int(stream_data.get("remaining_qty", "0")),
                original_qty=int(stream_data.get("original_qty", "0")),
                price=int(stream_data.get("price", "0")),
                is_taker=stream_data.get("is_taker", False),
                fee=int(stream_data.get("fee", "0")),
                is_bid=stream_data.get("is_bid", True),
                timestamp=float(stream_data.get("timestamp", time.time())),
            )
        except (ValueError, TypeError) as e:
            logger.warning(
                "ws_fill_parse_error", extra={"error": str(e), "data": str(stream_data)[:200]}
            )
            return

        if not event.order_digest:
            return

        logger.info(
            "fill_received",
            extra={
                "order_digest": event.order_digest,
                "filled_qty": str(event.filled_qty),
                "remaining_qty": str(event.remaining_qty),
                "is_taker": event.is_taker,
                "fee": str(event.fee),
                "is_bid": event.is_bid,
                "price": str(event.price),
            },
        )

        self._callback(event)


# ---------------------------------------------------------------------------
# Fallback polling listener (sync)
# ---------------------------------------------------------------------------


class PollingFillsListener:
    """Fallback: detect fills by comparing open orders periodically.

    This is a synchronous poller that runs in a thread or the main loop.
    """

    def __init__(
        self,
        exchange_client: Any,  # ExchangeClient — avoid circular import
        product_id: int,
        poll_interval_sec: int,
        callback: FillCallback,
    ) -> None:
        self._ec = exchange_client
        self._product_id = product_id
        self._interval = poll_interval_sec
        self._callback = callback
        self._running = False
        self._prev_snapshot: dict[str, int] = {}  # digest -> unfilled_amount

    def run_sync(self) -> None:
        """Blocking polling loop. Call from a thread."""
        self._running = True
        # Take initial snapshot
        self._prev_snapshot = self._take_snapshot()
        logger.info("polling_started", extra={"interval_sec": self._interval})

        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            try:
                current = self._take_snapshot()
                self._detect_fills(current)
                self._prev_snapshot = current
            except Exception as e:
                logger.warning("polling_error", extra={"error": str(e)})

    def stop(self) -> None:
        self._running = False

    def _take_snapshot(self) -> dict[str, int]:
        orders = self._ec.get_open_orders(self._product_id)
        return {o.digest: int(o.unfilled_amount) for o in orders}

    def _detect_fills(self, current: dict[str, int]) -> None:
        for digest, prev_remaining in self._prev_snapshot.items():
            cur_remaining = current.get(digest)
            if cur_remaining is None:
                # Order gone — fully filled or cancelled
                filled_qty = prev_remaining
                if filled_qty > 0:
                    event = FillEvent(
                        order_digest=digest,
                        filled_qty=filled_qty,
                        remaining_qty=0,
                        original_qty=prev_remaining,
                        price=0,  # unknown from polling
                        is_taker=False,  # assume maker (can't tell from polling)
                        fee=0,
                        is_bid=True,  # will be resolved by execution layer from state
                    )
                    self._callback(event)
            elif cur_remaining < prev_remaining:
                # Partial fill
                filled_qty = prev_remaining - cur_remaining
                event = FillEvent(
                    order_digest=digest,
                    filled_qty=filled_qty,
                    remaining_qty=cur_remaining,
                    original_qty=prev_remaining,
                    price=0,
                    is_taker=False,
                    fee=0,
                    is_bid=True,
                )
                self._callback(event)
