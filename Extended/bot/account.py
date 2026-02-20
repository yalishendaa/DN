"""Модуль работы с аккаунтом пользователя."""

from typing import List, Optional

from x10.perpetual.accounts import AccountModel
from x10.perpetual.balances import BalanceModel
from x10.perpetual.orders import OpenOrderModel, OrderSide, OrderType
from x10.perpetual.positions import PositionModel, PositionSide
from x10.perpetual.trading_client import PerpetualTradingClient
from x10.utils.http import WrappedApiResponse

from bot.websocket_manager import WebSocketManager


class AccountManager:
    """Менеджер для работы с аккаунтом пользователя."""

    def __init__(
        self,
        trading_client: PerpetualTradingClient,
        websocket_manager: Optional[WebSocketManager] = None,
    ):
        """
        Инициализация менеджера аккаунта.

        Args:
            trading_client: Торговый клиент Extended Exchange
            websocket_manager: Менеджер WebSocket для кэширования данных (опционально)
        """
        self._client = trading_client
        self._websocket_manager = websocket_manager

    async def get_user_info(self) -> WrappedApiResponse[AccountModel]:
        """
        Получить информацию об аккаунте пользователя.

        Returns:
            WrappedApiResponse[AccountModel]: Информация об аккаунте
        """
        return await self._client.account.get_account()

    async def get_balance(self, use_cache: bool = True) -> WrappedApiResponse[BalanceModel]:
        """
        Получить баланс аккаунта.

        Args:
            use_cache: Использовать кэш WebSocket, если доступен (по умолчанию True)

        Returns:
            WrappedApiResponse[BalanceModel]: Баланс пользователя
        """
        # Проверяем, можно ли использовать кэш WebSocket
        if use_cache and self._websocket_manager and self._websocket_manager.is_running:
            cached_balance = self._websocket_manager.get_cached_balance()
            if cached_balance is not None:
                # Возвращаем кэшированные данные в формате WrappedApiResponse
                from x10.utils.http import ResponseStatus

                return WrappedApiResponse(status=ResponseStatus.OK, data=cached_balance)

        # Fallback на REST API
        return await self._client.account.get_balance()

    async def get_positions(
        self,
        market_names: Optional[List[str]] = None,
        position_side: Optional[PositionSide] = None,
        use_cache: bool = True,
    ) -> WrappedApiResponse[List[PositionModel]]:
        """
        Получить открытые позиции.

        Args:
            market_names: Список названий рынков для фильтрации (опционально)
            position_side: Сторона позиции для фильтрации (опционально)
            use_cache: Использовать кэш WebSocket, если доступен (по умолчанию True)

        Returns:
            WrappedApiResponse[List[PositionModel]]: Список открытых позиций
        """
        # Проверяем, можно ли использовать кэш WebSocket
        if use_cache and self._websocket_manager and self._websocket_manager.is_running:
            cached_positions = self._websocket_manager.get_cached_positions()

            # Применяем фильтры, если указаны
            filtered_positions = cached_positions
            if market_names:
                filtered_positions = [p for p in filtered_positions if p.market in market_names]
            if position_side:
                filtered_positions = [p for p in filtered_positions if p.side == position_side]

            # Возвращаем кэшированные данные в формате WrappedApiResponse
            from x10.utils.http import ResponseStatus

            return WrappedApiResponse(status=ResponseStatus.OK, data=filtered_positions)

        # Fallback на REST API
        return await self._client.account.get_positions(
            market_names=market_names, position_side=position_side
        )

    async def get_open_orders(
        self,
        market_names: Optional[List[str]] = None,
        order_type: Optional[OrderType] = None,
        order_side: Optional[OrderSide] = None,
        use_cache: bool = True,
    ) -> WrappedApiResponse[List[OpenOrderModel]]:
        """
        Получить открытые ордера.

        Args:
            market_names: Список названий рынков для фильтрации (опционально)
            order_type: Тип ордера для фильтрации (опционально)
            order_side: Сторона ордера для фильтрации (опционально)
            use_cache: Использовать кэш WebSocket, если доступен (по умолчанию True)

        Returns:
            WrappedApiResponse[List[OpenOrderModel]]: Список открытых ордеров
        """
        # Проверяем, можно ли использовать кэш WebSocket
        if use_cache and self._websocket_manager and self._websocket_manager.is_running:
            cached_orders = self._websocket_manager.get_cached_orders()

            # Применяем фильтры, если указаны
            filtered_orders = cached_orders
            if market_names:
                filtered_orders = [o for o in filtered_orders if o.market in market_names]
            if order_type:
                filtered_orders = [o for o in filtered_orders if o.type == order_type]
            if order_side:
                filtered_orders = [o for o in filtered_orders if o.side == order_side]

            # Возвращаем кэшированные данные в формате WrappedApiResponse
            from x10.utils.http import ResponseStatus

            return WrappedApiResponse(status=ResponseStatus.OK, data=filtered_orders)

        # Fallback на REST API
        return await self._client.account.get_open_orders(
            market_names=market_names, order_type=order_type, order_side=order_side
        )
