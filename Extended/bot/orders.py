"""Модуль работы с ордерами."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from x10.perpetual.order_object import OrderTpslTriggerParam
from x10.perpetual.orders import (
    OpenOrderModel,
    OrderSide,
    OrderTpslType,
    PlacedOrderModel,
    SelfTradeProtectionLevel,
    TimeInForce,
)
from x10.perpetual.trading_client import PerpetualTradingClient
from x10.utils.http import WrappedApiResponse
from x10.utils.model import EmptyModel


class OrdersManager:
    """Менеджер для работы с ордерами."""

    def __init__(self, trading_client: PerpetualTradingClient):
        """
        Инициализация менеджера ордеров.

        Args:
            trading_client: Торговый клиент Extended Exchange
        """
        self._client = trading_client

    async def place_order(
        self,
        market_name: str,
        amount: Decimal,
        price: Decimal,
        side: OrderSide,
        post_only: bool = False,
        time_in_force: TimeInForce = TimeInForce.GTT,
        expire_time: Optional[datetime] = None,
        external_id: Optional[str] = None,
        reduce_only: bool = False,
        self_trade_protection_level: SelfTradeProtectionLevel = SelfTradeProtectionLevel.ACCOUNT,
        tp_sl_type: Optional[OrderTpslType] = None,
        take_profit: Optional[OrderTpslTriggerParam] = None,
        stop_loss: Optional[OrderTpslTriggerParam] = None,
    ) -> WrappedApiResponse[PlacedOrderModel]:
        """
        Выставить ордер.

        Args:
            market_name: Название рынка (например, "BTC-USD")
            amount: Количество синтетического актива
            price: Цена ордера
            side: Сторона ордера (BUY или SELL)
            post_only: Только maker ордер (не будет исполнен как taker)
            time_in_force: Время действия ордера (GTT, IOC, FOK)
            expire_time: Время истечения ордера (опционально)
            external_id: Внешний ID ордера (опционально)
            reduce_only: Только уменьшение позиции
            self_trade_protection_level: Уровень защиты от самоторговли
            tp_sl_type: Тип TP/SL (опционально)
            take_profit: Параметры тейк-профита (опционально)
            stop_loss: Параметры стоп-лосса (опционально)

        Returns:
            WrappedApiResponse[PlacedOrderModel]: Результат размещения ордера
        """
        return await self._client.place_order(
            market_name=market_name,
            amount_of_synthetic=amount,
            price=price,
            side=side,
            post_only=post_only,
            time_in_force=time_in_force,
            expire_time=expire_time,
            external_id=external_id,
            reduce_only=reduce_only,
            self_trade_protection_level=self_trade_protection_level,
            tp_sl_type=tp_sl_type,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )

    async def cancel_order(self, order_id: int) -> WrappedApiResponse[EmptyModel]:
        """
        Закрыть ордер по ID.

        Args:
            order_id: ID ордера

        Returns:
            WrappedApiResponse[EmptyModel]: Результат отмены ордера
        """
        return await self._client.orders.cancel_order(order_id=order_id)

    async def cancel_order_by_external_id(self, external_id: str) -> WrappedApiResponse[EmptyModel]:
        """
        Закрыть ордер по внешнему ID.

        Args:
            external_id: Внешний ID ордера

        Returns:
            WrappedApiResponse[EmptyModel]: Результат отмены ордера
        """
        return await self._client.orders.cancel_order_by_external_id(order_external_id=external_id)

    async def cancel_all_orders(
        self,
        market_name: Optional[str] = None,
        order_ids: Optional[List[int]] = None,
        external_order_ids: Optional[List[str]] = None,
        cancel_all: bool = False,
    ) -> WrappedApiResponse[EmptyModel]:
        """
        Закрыть все ордера или ордера по фильтрам.

        Args:
            market_name: Название рынка для фильтрации (опционально)
            order_ids: Список ID ордеров для отмены (опционально)
            external_order_ids: Список внешних ID ордеров для отмены (опционально)
            cancel_all: Отменить все ордера (если True)

        Returns:
            WrappedApiResponse[EmptyModel]: Результат массовой отмены
        """
        markets = [market_name] if market_name else None
        return await self._client.orders.mass_cancel(
            order_ids=order_ids,
            external_order_ids=external_order_ids,
            markets=markets,
            cancel_all=cancel_all,
        )

    async def get_order_status(self, order_id: int) -> WrappedApiResponse[OpenOrderModel]:
        """
        Получить статус ордера по ID.

        Args:
            order_id: ID ордера

        Returns:
            WrappedApiResponse[OpenOrderModel]: Информация об ордере
        """
        return await self._client.account.get_order_by_id(order_id=order_id)
