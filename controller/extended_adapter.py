"""Адаптер Extended Exchange — реализация ExchangeAdapter.

Обёртка вокруг ExtendedTradingBot, нормализующая все данные
в единый формат для контроллера.
"""

from __future__ import annotations

import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Добавляем Extended в sys.path для импорта
_EXTENDED_ROOT = Path(__file__).resolve().parent.parent / "Extended"
if str(_EXTENDED_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTENDED_ROOT))
_EXTENDED_SDK = _EXTENDED_ROOT / "python_sdk"
if str(_EXTENDED_SDK) not in sys.path:
    sys.path.insert(0, str(_EXTENDED_SDK))

from bot.config import ExtendedBotConfig
from bot.trading_bot import ExtendedTradingBot
from x10.perpetual.orders import OrderSide
from x10.perpetual.positions import PositionSide

from controller.interface import ExchangeAdapter
from controller.models import (
    NormalizedBalance,
    NormalizedOrder,
    NormalizedPosition,
    PlacedOrderResult,
    PositionDirection,
    Side,
)

logger = logging.getLogger("dn.extended")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decimal_to_float(v: Decimal | None) -> float:
    return float(v) if v is not None else 0.0


def _side_to_enum(side: OrderSide) -> Side:
    return Side.BUY if side == OrderSide.BUY else Side.SELL


def _position_direction(side: PositionSide | None, size: float) -> PositionDirection:
    if size == 0 or side is None:
        return PositionDirection.FLAT
    if side == PositionSide.LONG:
        return PositionDirection.LONG
    return PositionDirection.SHORT


# ---------------------------------------------------------------------------
# ExtendedAdapter
# ---------------------------------------------------------------------------


class ExtendedAdapter(ExchangeAdapter):
    """Адаптер Extended Exchange."""

    def __init__(
        self,
        env_file: str | None = None,
        instrument_map: dict[str, str] | None = None,
    ):
        """
        Args:
            env_file: Путь к .env файлу Extended (по умолчанию Extended/.env).
            instrument_map: Маппинг логических символов → market_name.
                            Напр. {"BTC-PERP": "BTC-USD"}.
        """
        self._env_file = env_file or str(_EXTENDED_ROOT / ".env")
        self._instrument_map = instrument_map or {}
        self._bot: Optional[ExtendedTradingBot] = None

    # -- Properties ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "extended"

    @property
    def bot(self) -> ExtendedTradingBot:
        if self._bot is None:
            raise RuntimeError("Адаптер не инициализирован. Вызовите initialize() первым.")
        return self._bot

    # -- Маппинг инструментов ------------------------------------------------

    def _to_market_name(self, instrument: str) -> str:
        """Логический символ → market_name на Extended."""
        market = self._instrument_map.get(instrument)
        if not market:
            raise ValueError(
                f"Инструмент '{instrument}' не найден в instrument_map. "
                f"Доступные: {list(self._instrument_map.keys())}"
            )
        return market

    # -- Жизненный цикл ------------------------------------------------------

    async def initialize(self) -> None:
        config = ExtendedBotConfig.from_env(env_file=self._env_file)
        self._bot = ExtendedTradingBot(config)
        logger.info("Extended adapter initialized (env=%s)", self._env_file)

    async def close(self) -> None:
        if self._bot:
            await self._bot.close()
            logger.info("Extended adapter closed")

    # -- Баланс --------------------------------------------------------------

    async def get_balance(self) -> NormalizedBalance:
        # Для торговых решений нужен актуальный available_for_trade, а не кэш.
        resp = await self.bot.account.get_balance(use_cache=False)
        bal = resp.data
        return NormalizedBalance(
            equity=_decimal_to_float(bal.equity),
            available=_decimal_to_float(bal.available_for_trade),
            currency=bal.collateral_name or "USD",
        )

    # -- Позиция -------------------------------------------------------------

    async def get_position(self, instrument: str) -> NormalizedPosition:
        market_name = self._to_market_name(instrument)
        resp = await self.bot.account.get_positions(market_names=[market_name], use_cache=True)
        positions = resp.data or []

        if not positions:
            return NormalizedPosition(
                instrument=instrument,
                size=0.0,
                direction=PositionDirection.FLAT,
            )

        pos = positions[0]
        raw_size = _decimal_to_float(pos.size)
        size = raw_size if pos.side == PositionSide.LONG else -raw_size
        direction = _position_direction(pos.side, raw_size)

        return NormalizedPosition(
            instrument=instrument,
            size=size,
            direction=direction,
            entry_price=_decimal_to_float(pos.open_price),
            mark_price=_decimal_to_float(pos.mark_price),
            unrealised_pnl=_decimal_to_float(pos.unrealised_pnl),
        )

    # -- Открытые ордера -----------------------------------------------------

    async def get_open_orders(self, instrument: str) -> list[NormalizedOrder]:
        market_name = self._to_market_name(instrument)
        resp = await self.bot.account.get_open_orders(market_names=[market_name], use_cache=True)
        orders = resp.data or []
        result = []
        for o in orders:
            result.append(
                NormalizedOrder(
                    id=f"ext:{o.id}",
                    instrument=instrument,
                    side=_side_to_enum(o.side),
                    price=_decimal_to_float(o.price),
                    amount=_decimal_to_float(o.qty),
                    filled=_decimal_to_float(o.filled_qty),
                    post_only=o.post_only,
                    reduce_only=o.reduce_only,
                )
            )
        return result

    # -- Референсная цена ----------------------------------------------------

    async def get_reference_price(self, instrument: str) -> float:
        """Mid price из ордербука или mark price из статистики рынка."""
        bid, ask = await self.get_best_bid_ask(instrument)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2

        market_name = self._to_market_name(instrument)
        # Fallback: mark price из статистики рынка
        try:
            resp = await self.bot.client.markets_info.get_market_statistics(market_name=market_name)
            if resp.data and resp.data.mark_price:
                return _decimal_to_float(resp.data.mark_price)
        except Exception as e:
            logger.warning("Failed to get market statistics for %s: %s", market_name, e)

        # Fallback: mark price из позиции
        pos = await self.get_position(instrument)
        if pos.mark_price > 0:
            return pos.mark_price

        return 0.0

    async def get_best_bid_ask(self, instrument: str) -> tuple[float, float]:
        """Лучшие bid/ask из websocket orderbook или REST snapshot."""
        market_name = self._to_market_name(instrument)
        try:
            # Пробуем из живого ордербука (если подписан)
            best_bid, best_ask = self.bot.markets.get_best_bid_ask(market_name)
            if best_bid and best_ask:
                return _decimal_to_float(best_bid.price), _decimal_to_float(best_ask.price)
        except Exception:
            pass

        # Fallback: REST orderbook snapshot
        try:
            resp = await self.bot.markets.get_orderbook_snapshot(market_name)
            ob = resp.data
            if ob and ob.bid and ob.ask and len(ob.bid) > 0 and len(ob.ask) > 0:
                # OrderbookUpdateModel имеет bid/ask (не bids/asks) — списки OrderbookQuantityModel
                best_bid_price = _decimal_to_float(ob.bid[0].price)
                best_ask_price = _decimal_to_float(ob.ask[0].price)
                return best_bid_price, best_ask_price
        except Exception as e:
            logger.warning("Failed to get orderbook for %s: %s", market_name, e)

        return 0.0, 0.0

    # -- Выставление ордера --------------------------------------------------

    async def place_limit_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        post_only: bool = True,
        reduce_only: bool = False,
        external_id: Optional[str] = None,
    ) -> PlacedOrderResult:
        market_name = self._to_market_name(instrument)
        order_side = OrderSide.BUY if side == Side.BUY else OrderSide.SELL

        try:
            # Получаем информацию о рынке для округления цены до tick size
            market_info = await self.bot.markets.find_market(market_name)
            if not market_info:
                return PlacedOrderResult(
                    id="",
                    success=False,
                    error=f"Market {market_name} not found",
                )

            # Округляем цену до допустимой точности (tick size)
            price_decimal = Decimal(str(price))
            rounded_price = market_info.trading_config.round_price(price_decimal)

            # Округляем объём до шага min_order_size_change
            tc = market_info.trading_config
            amount_decimal = Decimal(str(amount))
            rounded_amount = tc.round_order_size(amount_decimal)
            amount = float(rounded_amount)

            resp = await self.bot.orders.place_order(
                market_name=market_name,
                amount=rounded_amount,
                price=rounded_price,
                side=order_side,
                post_only=post_only,
                reduce_only=reduce_only,
                external_id=external_id,
            )
            order_data = resp.data
            order_id = f"ext:{order_data.id}"
            # Для post-only ордера проверяем, что он действительно появился в open orders.
            # Это надёжнее, чем дергать отдельный status endpoint, который может флапать.
            if post_only:
                open_orders = await self.get_open_orders(instrument)
                if not any(o.id == order_id for o in open_orders):
                    error = "Extended post-only order not visible in open orders right after placement"
                    logger.error(
                        "Order not visible on Extended after placement: %s %s %s @ %s, id=%s",
                        side.value,
                        amount,
                        instrument,
                        price,
                        order_id,
                    )
                    return PlacedOrderResult(id="", success=False, error=error)

            logger.info(
                "Order placed on Extended: %s %s %s @ %s, id=%s",
                side.value,
                amount,
                instrument,
                price,
                order_id,
            )
            return PlacedOrderResult(id=order_id, success=True)
        except Exception as e:
            logger.error("Failed to place order on Extended: %s", e)
            return PlacedOrderResult(id="", success=False, error=str(e))

    # -- Отмена ордера -------------------------------------------------------

    async def cancel_order(self, instrument: str, order_id: str) -> bool:
        try:
            # order_id имеет формат "ext:12345"
            raw_id = int(order_id.replace("ext:", ""))
            await self.bot.orders.cancel_order(order_id=raw_id)
            logger.info("Order cancelled on Extended: %s", order_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel order %s on Extended: %s", order_id, e)
            return False

    async def cancel_all_orders(self, instrument: str) -> int:
        market_name = self._to_market_name(instrument)
        try:
            # Получаем текущие ордера для подсчёта
            orders = await self.get_open_orders(instrument)
            count = len(orders)
            await self.bot.orders.cancel_all_orders(market_name=market_name)
            logger.info("Cancelled %d orders on Extended for %s", count, instrument)
            return count
        except Exception as e:
            logger.error("Failed to cancel all orders on Extended: %s", e)
            return 0
