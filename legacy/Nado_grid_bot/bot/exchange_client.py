"""Wrapper around Nado Python SDK for grid bot operations."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from nado_protocol.client import NadoClient, NadoClientMode, create_nado_client
from nado_protocol.engine_client.types.execute import (
    CancelOrdersParams,
    PlaceOrderParams,
)
from nado_protocol.engine_client.types.models import ProductBookInfo
from nado_protocol.engine_client.types.query import (
    AllProductsData,
    OrderData,
    SubaccountInfoData,
    SubaccountOpenOrdersData,
)
from nado_protocol.utils.bytes32 import subaccount_to_bytes32, subaccount_to_hex
from nado_protocol.utils.execute import OrderParams
from nado_protocol.utils.expiration import OrderType, get_expiration_timestamp
from nado_protocol.utils.math import round_x18
from nado_protocol.utils.nonce import gen_order_nonce
from nado_protocol.utils.order import build_appendix
from nado_protocol.utils.subaccount import SubaccountParams

from bot.config import BotConfig

logger = logging.getLogger("nado_grid")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class BookInfo:
    """Extracted market specification for a product."""

    price_increment_x18: int
    size_increment: int
    min_size: int


def round_up_x18(x: int, y: int) -> int:
    """Round *x* UP to the nearest multiple of *y*."""
    remainder = x % y
    if remainder == 0:
        return x
    return x + (y - remainder)


# ---------------------------------------------------------------------------
# Error codes we handle explicitly
# ---------------------------------------------------------------------------

ERR_AMOUNT_TOO_SMALL = 2003
ERR_INVALID_AMOUNT_INCREMENT = 2004
ERR_INVALID_PRICE_INCREMENT = 2005
ERR_ORACLE_PRICE_DIFF = 2007
ERR_POST_ONLY_CROSSES = 2008
ERR_RATE_LIMIT = 3001


class NadoApiError(Exception):
    """Wraps an error response from the Nado engine."""

    def __init__(self, code: int, message: str, raw: Any = None):
        self.code = code
        self.message = message
        self.raw = raw
        super().__init__(f"[{code}] {message}")


def _response_to_dict(resp: Any) -> dict[str, Any]:
    """Безопасно привести ответ SDK к dict для проверки статуса."""
    if isinstance(resp, dict):
        return resp

    model_dump = getattr(resp, "model_dump", None)
    if callable(model_dump):
        return model_dump()

    to_dict = getattr(resp, "dict", None)
    if callable(to_dict):
        return to_dict()

    return {}


# ---------------------------------------------------------------------------
# ExchangeClient
# ---------------------------------------------------------------------------


class ExchangeClient:
    """High-level wrapper around Nado SDK for grid-bot needs."""

    def __init__(self, config: BotConfig) -> None:
        mode_map = {
            "mainnet": NadoClientMode.MAINNET,
            "testnet": NadoClientMode.TESTNET,
            "devnet": NadoClientMode.DEVNET,
        }
        mode = mode_map[config.network]
        self._client: NadoClient = create_nado_client(
            mode=mode,
            signer=config.private_key,
        )
        self._config = config
        self._owner: str = self._client.context.engine_client.signer.address
        self._subaccount_params = SubaccountParams(
            subaccount_owner=self._owner,
            subaccount_name=config.subaccount_name,
        )
        self._sender_hex: str = subaccount_to_hex(self._subaccount_params)

        logger.info(
            "sdk_init_success",
            extra={"mode": config.network},
        )

    # -- Properties ----------------------------------------------------------

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def sender_hex(self) -> str:
        return self._sender_hex

    @property
    def client(self) -> NadoClient:
        return self._client

    # -- Market data ---------------------------------------------------------

    def get_mark_price(self, product_id: int) -> int:
        """Return mark_price_x18 as int."""
        prices = self._client.perp.get_prices(product_id)
        mark = int(prices.mark_price_x18)
        logger.info(
            "mark_price_fetched",
            extra={
                "product_id": product_id,
                "mark_price_x18": str(mark),
                "update_time": prices.update_time,
            },
        )
        return mark

    def get_book_info(self, product_id: int) -> BookInfo:
        """Fetch product specification (tick size, min size, size increment)."""
        all_products: AllProductsData = self._client.market.get_all_engine_markets()
        for prod in all_products.perp_products:
            if prod.product_id == product_id:
                bi: ProductBookInfo = prod.book_info
                info = BookInfo(
                    price_increment_x18=int(bi.price_increment_x18),
                    size_increment=int(bi.size_increment),
                    min_size=int(bi.min_size),
                )
                logger.info(
                    "book_info_fetched",
                    extra={
                        "product_id": product_id,
                        "price_increment_x18": str(info.price_increment_x18),
                        "size_increment": str(info.size_increment),
                        "min_size": str(info.min_size),
                    },
                )
                return info
        raise ValueError(f"Product {product_id} not found in perp_products")

    def get_open_orders(self, product_id: int) -> list[OrderData]:
        """Return list of open orders for configured subaccount."""
        data: SubaccountOpenOrdersData = self._client.market.get_subaccount_open_orders(
            product_id, self._sender_hex
        )
        return data.orders

    def get_subaccount_summary(self) -> SubaccountInfoData:
        """Fetch full subaccount info including positions."""
        return self._client.subaccount.get_engine_subaccount_summary(self._sender_hex)

    def get_perp_position_amount(self, product_id: int) -> int:
        """Return current perp position amount (x18). 0 if none."""
        summary = self.get_subaccount_summary()
        for pb in summary.perp_balances:
            if pb.product_id == product_id:
                return int(pb.balance.amount)
        return 0

    # -- Order placement -----------------------------------------------------

    def place_post_only_order(
        self,
        product_id: int,
        price_x18: int,
        amount: int,
        reduce_only: bool = False,
        order_id: Optional[int] = None,
    ) -> tuple[str, Any]:
        """Place a POST_ONLY limit order.

        Args:
            product_id: Product ID (2 for BTC-PERP).
            price_x18: Price in x18 format.
            amount: Signed amount in x18 (+buy, -sell).
            reduce_only: Whether to set reduce-only flag.
            order_id: Optional custom order id echoed in fills.

        Returns:
            Tuple of (order_digest, response).

        Raises:
            NadoApiError: On known API errors.
        """
        appendix = build_appendix(OrderType.POST_ONLY, reduce_only=reduce_only)

        order = OrderParams(
            sender=SubaccountParams(
                subaccount_owner=self._owner,
                subaccount_name=self._config.subaccount_name,
            ),
            priceX18=price_x18,
            amount=amount,
            expiration=get_expiration_timestamp(self._config.order_ttl_sec),
            appendix=appendix,
            nonce=gen_order_nonce(),
        )

        # Compute digest before placement (need bytes32 sender)
        order_for_digest = OrderParams(
            sender=subaccount_to_bytes32(
                SubaccountParams(
                    subaccount_owner=self._owner,
                    subaccount_name=self._config.subaccount_name,
                )
            ),
            priceX18=order.priceX18,
            amount=order.amount,
            expiration=order.expiration,
            appendix=order.appendix,
            nonce=order.nonce,
        )
        digest = self._client.context.engine_client.get_order_digest(order_for_digest, product_id)

        params: dict = {
            "product_id": product_id,
            "order": order,
        }
        # spot_leverage only applies to spot products, skip for perp
        # Only include if explicitly True (for spot products)
        if self._config.spot_leverage:
            params["spot_leverage"] = True
        if order_id is not None:
            params["id"] = order_id

        resp = self._client.market.place_order(params)

        # Check for failure
        resp_dict = _response_to_dict(resp)
        status = getattr(resp, "status", "success")
        if status == "failure":
            err_code = resp_dict.get("error_code", 0)
            err_msg = resp_dict.get("error", str(resp_dict))
            raise NadoApiError(err_code, err_msg, resp_dict)

        side = "buy" if amount > 0 else "sell"
        logger.info(
            "order_placed",
            extra={
                "product_id": product_id,
                "side": side,
                "price_x18": str(price_x18),
                "amount": str(amount),
                "digest": digest,
                "status": "success",
                "reduce_only": reduce_only,
            },
        )
        return digest, resp

    def place_ioc_order(
        self,
        product_id: int,
        price_x18: int,
        amount: int,
        reduce_only: bool = False,
        order_id: Optional[int] = None,
    ) -> tuple[str, Any]:
        """Place an IOC (taker) limit order.

        Used as a market-like order: price is chosen aggressively outside the
        spread, IOC guarantees либо исполнение, либо немедленная отмена.
        """

        appendix = build_appendix(OrderType.IOC, reduce_only=reduce_only)

        order = OrderParams(
            sender=self._subaccount_params,
            priceX18=price_x18,
            amount=amount,
            expiration=get_expiration_timestamp(self._config.order_ttl_sec),
            appendix=appendix,
            nonce=gen_order_nonce(),
        )

        order_for_digest = OrderParams(
            sender=subaccount_to_bytes32(self._subaccount_params),
            priceX18=order.priceX18,
            amount=order.amount,
            expiration=order.expiration,
            appendix=order.appendix,
            nonce=order.nonce,
        )
        digest = self._client.context.engine_client.get_order_digest(order_for_digest, product_id)

        params: dict = {
            "product_id": product_id,
            "order": order,
        }
        if self._config.spot_leverage:
            params["spot_leverage"] = True
        if order_id is not None:
            params["id"] = order_id

        resp = self._client.market.place_order(params)

        resp_dict = _response_to_dict(resp)
        status = getattr(resp, "status", "success")
        if status == "failure":
            err_code = resp_dict.get("error_code", 0)
            err_msg = resp_dict.get("error", str(resp_dict))
            raise NadoApiError(err_code, err_msg, resp_dict)

        side = "buy" if amount > 0 else "sell"
        logger.info(
            "order_placed",
            extra={
                "product_id": product_id,
                "side": side,
                "price_x18": str(price_x18),
                "amount": str(amount),
                "digest": digest,
                "status": "success",
                "reduce_only": reduce_only,
                "type": "ioc",
            },
        )
        return digest, resp

    # -- Order cancellation --------------------------------------------------

    def cancel_order(self, product_id: int, digest: str) -> Any:
        """Cancel a single order by digest."""
        resp = self._client.market.cancel_orders(
            {
                "productIds": [product_id],
                "digests": [digest],
                "sender": self._sender_hex,
            }
        )
        logger.info("order_cancelled", extra={"digest": digest, "status": "success"})
        return resp

    def cancel_all_orders(self, product_id: int) -> Any:
        """Cancel all orders for a product."""
        resp = self._client.market.cancel_product_orders(
            {
                "productIds": [product_id],
                "sender": self._sender_hex,
            }
        )
        logger.info("all_orders_cancelled", extra={"product_id": product_id})
        return resp

    # -- Retry helper --------------------------------------------------------

    def retry_on_rate_limit(self, fn, *args, max_retries: int = 5, **kwargs):
        """Execute *fn* with exponential backoff on rate-limit errors."""
        for attempt in range(max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except NadoApiError as e:
                if e.code == ERR_RATE_LIMIT and attempt < max_retries:
                    wait = self._config.backoff_base_sec * (2**attempt)
                    logger.warning(
                        "rate_limit_backoff",
                        extra={"err_code": e.code, "attempt": attempt, "wait_sec": wait},
                    )
                    time.sleep(wait)
                else:
                    raise
            except Exception as e:
                if attempt < max_retries:
                    wait = self._config.backoff_base_sec * (2**attempt)
                    logger.warning(
                        "network_error_retry",
                        extra={"error": str(e), "attempt": attempt, "wait_sec": wait},
                    )
                    time.sleep(wait)
                else:
                    raise
