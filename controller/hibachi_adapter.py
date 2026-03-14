"""Адаптер Hibachi Exchange — реализация ExchangeAdapter.

Обёртка вокруг HibachiApiClient (синхронный REST SDK).
Все блокирующие вызовы оборачиваются в asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

# SDK добавляется в конец sys.path, чтобы не затенять stdlib/site-packages.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_HIBACHI_SDK = _REPO_ROOT / "Hibachi" / "hibachi-sdk" / "python"
_HIBACHI_SDK_STR = str(_HIBACHI_SDK.resolve())
if _HIBACHI_SDK_STR not in sys.path:
    sys.path.append(_HIBACHI_SDK_STR)

from controller.interface import ExchangeAdapter
from controller.models import (
    NormalizedBalance,
    NormalizedOrder,
    NormalizedPosition,
    PlacedOrderResult,
    PositionDirection,
    Side,
)

logger = logging.getLogger("dn.hibachi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_to_float(val: str | None) -> float:
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _position_direction(direction: str, quantity: float) -> PositionDirection:
    if quantity == 0:
        return PositionDirection.FLAT
    if direction.lower() == "long":
        return PositionDirection.LONG
    if direction.lower() == "short":
        return PositionDirection.SHORT
    return PositionDirection.FLAT


# ---------------------------------------------------------------------------
# HibachiAdapter
# ---------------------------------------------------------------------------


class HibachiAdapter(ExchangeAdapter):
    """Адаптер Hibachi Exchange."""

    def __init__(
        self,
        env_file: str | None = None,
        instrument_map: dict[str, str] | None = None,
    ):
        """
        Args:
            env_file: Путь к .env файлу Hibachi (по умолчанию Hibachi/.env).
            instrument_map: Маппинг логических символов → Hibachi symbol.
                            Напр. {"BTC-PERP": "BTC/USDT-P"}.
        """
        self._env_file = env_file or str(_REPO_ROOT / "Hibachi" / ".env")
        self._instrument_map = instrument_map or {}
        self._client = None  # HibachiApiClient после initialize()
        self._max_fees_percent: float = 0.001  # Будет пересчитан из exchange info

    # -- Properties ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "hibachi"

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Адаптер не инициализирован. Вызовите initialize() первым.")
        return self._client

    # -- Маппинг инструментов ------------------------------------------------

    def _to_hibachi_symbol(self, instrument: str) -> str:
        symbol = self._instrument_map.get(instrument)
        if not symbol:
            raise ValueError(
                f"Инструмент '{instrument}' не найден в instrument_map. "
                f"Доступные: {list(self._instrument_map.keys())}"
            )
        return symbol

    # -- Жизненный цикл ------------------------------------------------------

    async def initialize(self) -> None:
        import os
        from dotenv import load_dotenv

        load_dotenv(self._env_file)

        environment = os.getenv("ENVIRONMENT", "production").lower()

        api_url = os.environ.get(
            f"HIBACHI_API_ENDPOINT_{environment.upper()}",
            "https://api.hibachi.xyz",
        )
        data_api_url = os.environ.get(
            f"HIBACHI_DATA_API_ENDPOINT_{environment.upper()}",
            "https://data-api.hibachi.xyz",
        )
        api_key = os.environ.get(f"HIBACHI_API_KEY_{environment.upper()}")
        account_id_str = os.environ.get(f"HIBACHI_ACCOUNT_ID_{environment.upper()}")
        private_key = os.environ.get(f"HIBACHI_PRIVATE_KEY_{environment.upper()}")

        if not api_key:
            raise ValueError("HIBACHI_API_KEY не задан в .env")
        if not account_id_str:
            raise ValueError("HIBACHI_ACCOUNT_ID не задан в .env")
        if not private_key:
            raise ValueError("HIBACHI_PRIVATE_KEY не задан в .env")

        try:
            account_id = int(account_id_str)
        except ValueError as e:
            raise ValueError(f"HIBACHI_ACCOUNT_ID невалидное значение: '{account_id_str}'") from e

        from hibachi_xyz import HibachiApiClient

        self._client = HibachiApiClient(
            api_url=api_url,
            data_api_url=data_api_url,
            api_key=api_key,
            account_id=account_id,
            private_key=private_key,
        )

        # Загружаем контракты и вычисляем max_fees_percent один раз.
        exchange_info = await self._run_sync(self._client.get_exchange_info)
        taker_fee = _str_to_float(exchange_info.feeConfig.tradeTakerFeeRate)
        self._max_fees_percent = max(taker_fee * 2.0, 0.001)

        logger.info(
            "Hibachi adapter initialized (env=%s, max_fees_pct=%.6f)",
            self._env_file,
            self._max_fees_percent,
        )

    async def close(self) -> None:
        logger.info("Hibachi adapter closed")

    # -- Async обёртка для sync SDK ------------------------------------------

    async def _run_sync(self, fn, *args, **kwargs):
        """Запуск синхронной функции в thread pool."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    # -- Баланс --------------------------------------------------------------

    async def get_balance(self) -> NormalizedBalance:
        info = await self._run_sync(self.client.get_account_info)
        equity = _str_to_float(info.balance)
        # maximalWithdraw — свободные средства (ближайший аналог available_for_trade)
        available = _str_to_float(info.maximalWithdraw)
        return NormalizedBalance(
            equity=equity,
            available=available,
            currency="USDT",
        )

    # -- Позиция -------------------------------------------------------------

    async def get_position(self, instrument: str) -> NormalizedPosition:
        hibachi_symbol = self._to_hibachi_symbol(instrument)
        info = await self._run_sync(self.client.get_account_info)

        for pos in info.positions:
            if pos.symbol == hibachi_symbol:
                qty = _str_to_float(pos.quantity)
                direction = _position_direction(pos.direction, qty)
                # Если direction неизвестный и qty > 0 — безопаснее вернуть 0.
                if direction == PositionDirection.FLAT:
                    size = 0.0
                elif direction == PositionDirection.LONG:
                    size = qty
                else:
                    size = -qty
                unrealised = _str_to_float(pos.unrealizedTradingPnl) + _str_to_float(
                    pos.unrealizedFundingPnl
                )
                return NormalizedPosition(
                    instrument=instrument,
                    size=size,
                    direction=direction,
                    entry_price=_str_to_float(pos.openPrice),
                    mark_price=_str_to_float(pos.markPrice),
                    unrealised_pnl=unrealised,
                )

        return NormalizedPosition(
            instrument=instrument,
            size=0.0,
            direction=PositionDirection.FLAT,
        )

    # -- Открытые ордера -----------------------------------------------------

    # Импорт на уровне метода (единожды при первом вызове).
    # Вынесен за пределы цикла, чтобы не делать lookup на каждой итерации.
    _HibachiOrderFlags = None

    def _get_hibachi_order_flags(self):
        if HibachiAdapter._HibachiOrderFlags is None:
            from hibachi_xyz.types import OrderFlags as _OF
            HibachiAdapter._HibachiOrderFlags = _OF
        return HibachiAdapter._HibachiOrderFlags

    async def get_open_orders(self, instrument: str) -> list[NormalizedOrder]:
        hibachi_symbol = self._to_hibachi_symbol(instrument)
        resp = await self._run_sync(self.client.get_pending_orders)
        HibachiOrderFlags = self._get_hibachi_order_flags()
        result = []
        for o in resp.orders:
            if o.symbol != hibachi_symbol:
                continue
            # Side: BID = buy, ASK = sell
            side_val = str(o.side.value).upper()
            norm_side = Side.BUY if side_val in ("BID", "BUY") else Side.SELL
            price = _str_to_float(o.price)
            total_qty = _str_to_float(o.totalQuantity)
            available_qty = _str_to_float(o.availableQuantity)
            filled = total_qty - available_qty
            post_only = o.orderFlags == HibachiOrderFlags.PostOnly
            reduce_only = o.orderFlags == HibachiOrderFlags.ReduceOnly
            result.append(
                NormalizedOrder(
                    id=f"hibachi:{o.orderId}",
                    instrument=instrument,
                    side=norm_side,
                    price=price,
                    amount=total_qty,
                    filled=max(0.0, filled),
                    post_only=post_only,
                    reduce_only=reduce_only,
                )
            )
        return result

    # -- Референсная цена ----------------------------------------------------

    async def get_reference_price(self, instrument: str) -> float:
        """Возвращает mid-price (bid+ask)/2, либо mark price как fallback."""
        hibachi_symbol = self._to_hibachi_symbol(instrument)
        try:
            prices = await self._run_sync(self.client.get_prices, hibachi_symbol)
            bid = _str_to_float(prices.bidPrice)
            ask = _str_to_float(prices.askPrice)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
            mark = _str_to_float(prices.markPrice)
            if mark > 0:
                return mark
        except Exception as e:
            logger.warning("Failed to get prices for %s: %s", hibachi_symbol, e)
        return 0.0

    async def get_best_bid_ask(self, instrument: str) -> tuple[float, float]:
        hibachi_symbol = self._to_hibachi_symbol(instrument)
        try:
            prices = await self._run_sync(self.client.get_prices, hibachi_symbol)
            bid = _str_to_float(prices.bidPrice)
            ask = _str_to_float(prices.askPrice)
            return bid, ask
        except Exception as e:
            logger.warning("Failed to get bid/ask for %s: %s", hibachi_symbol, e)
            return 0.0, 0.0

    # -- Выставление ордера --------------------------------------------------

    async def _place_order_with_flags(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        order_flags,
        log_tag: str = "",
    ) -> PlacedOrderResult:
        """Внутренний метод: выставить лимитку с явно заданными order_flags."""
        hibachi_symbol = self._to_hibachi_symbol(instrument)
        from hibachi_xyz.types import Side as HibachiSide

        hibachi_side = HibachiSide.BID if side == Side.BUY else HibachiSide.ASK
        try:
            nonce, order_id = await self._run_sync(
                self.client.place_limit_order,
                hibachi_symbol,
                amount,
                price,
                hibachi_side,
                self._max_fees_percent,
                None,   # trigger_price
                None,   # creation_deadline
                order_flags,
            )
            order_str_id = f"hibachi:{order_id}"
            logger.info(
                "Order placed on Hibachi%s: %s %s %s @ %s, id=%s",
                f" [{log_tag}]" if log_tag else "",
                side.value,
                amount,
                instrument,
                price,
                order_str_id,
            )
            return PlacedOrderResult(id=order_str_id, success=True)
        except Exception as e:
            logger.error("Failed to place order on Hibachi: %s", e)
            return PlacedOrderResult(id="", success=False, error=str(e))

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
        if external_id:
            logger.warning(
                "Hibachi не поддерживает клиентские ID ордеров; external_id='%s' будет проигнорирован",
                external_id,
            )
        from hibachi_xyz.types import OrderFlags

        # Приоритет флагов: reduce_only > post_only
        if reduce_only:
            order_flags = OrderFlags.ReduceOnly
        elif post_only:
            order_flags = OrderFlags.PostOnly
        else:
            # Агрессивная лимитка без флага — рестинг-ордер по рыночной цене.
            # Для taker-семантики (немедленное исполнение) используйте place_ioc_order.
            order_flags = None

        return await self._place_order_with_flags(
            instrument, side, price, amount, order_flags,
        )

    async def place_ioc_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        reduce_only: bool = False,
    ) -> PlacedOrderResult:
        """IOC-ордер: исполняется немедленно или отменяется.

        Используется контроллером в _place_ioc для secondary-ноги Hibachi,
        чтобы гарантировать немедленное исполнение и не оставить повисших ордеров.
        """
        from hibachi_xyz.types import OrderFlags

        # ReduceOnly имеет приоритет над Ioc; если нужны оба — биржа не поддерживает
        # их комбинацию, поэтому используем ReduceOnly (он уже подразумевает уменьшение
        # позиции, а IOC-семантика обеспечивается агрессивной ценой).
        order_flags = OrderFlags.ReduceOnly if reduce_only else OrderFlags.Ioc

        return await self._place_order_with_flags(
            instrument, side, price, amount, order_flags, log_tag="IOC",
        )

    # -- Отмена ордера -------------------------------------------------------

    async def cancel_order(self, instrument: str, order_id: str) -> bool:
        try:
            if not order_id.startswith("hibachi:"):
                logger.error(
                    "cancel_order: неверный формат order_id='%s' (ожидается 'hibachi:<int>')",
                    order_id,
                )
                return False
            raw_id = int(order_id.removeprefix("hibachi:"))
            await self._run_sync(self.client.cancel_order, raw_id)
            logger.info("Order cancelled on Hibachi: %s", order_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel order %s on Hibachi: %s", order_id, e)
            return False

    async def cancel_all_orders(self, instrument: str) -> int:
        """Отменить все ордера по инструменту поштучно.

        SDK-метод cancel_all_orders игнорирует contractId и отменяет ВСЕ ордера
        аккаунта, что нарушает изоляцию по инструменту. Поэтому используем
        поштучную отмену только нужных ордеров.
        """
        try:
            orders = await self.get_open_orders(instrument)
            count = 0
            for order in orders:
                if await self.cancel_order(instrument, order.id):
                    count += 1
            logger.info(
                "Cancelled %d/%d orders on Hibachi for %s",
                count,
                len(orders),
                instrument,
            )
            return count
        except Exception as e:
            logger.error("Failed to cancel all orders on Hibachi: %s", e)
            return 0
