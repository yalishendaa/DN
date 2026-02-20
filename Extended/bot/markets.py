"""Модуль работы с рынками и ордербуком."""

from typing import Dict, Optional

from x10.perpetual.configuration import EndpointConfig
from x10.perpetual.markets import MarketModel
from x10.perpetual.orderbook import OrderBook, OrderBookEntry
from x10.perpetual.orderbooks import OrderbookUpdateModel
from x10.perpetual.trading_client import PerpetualTradingClient
from x10.utils.http import WrappedApiResponse


class MarketsManager:
    """Менеджер для работы с рынками и ордербуком."""

    def __init__(self, trading_client: PerpetualTradingClient, endpoint_config: EndpointConfig):
        """
        Инициализация менеджера рынков.

        Args:
            trading_client: Торговый клиент Extended Exchange
            endpoint_config: Конфигурация эндпоинта
        """
        self._client = trading_client
        self._config = endpoint_config
        self._markets_cache: Optional[Dict[str, MarketModel]] = None
        self._orderbooks: Dict[str, OrderBook] = {}

    async def find_market(self, market_name: str) -> Optional[MarketModel]:
        """
        Найти рынок по имени.

        Args:
            market_name: Название рынка (например, "BTC-USD")

        Returns:
            Optional[MarketModel]: Модель рынка или None, если не найден
        """
        if self._markets_cache is None:
            markets_response = await self._client.markets_info.get_markets_dict()
            self._markets_cache = markets_response

        return self._markets_cache.get(market_name)

    async def get_market_info(self, market_name: str) -> WrappedApiResponse[MarketModel]:
        """
        Получить информацию о рынке.

        Args:
            market_name: Название рынка

        Returns:
            WrappedApiResponse[MarketModel]: Информация о рынке
        """
        markets_response = await self._client.markets_info.get_markets(market_names=[market_name])
        if markets_response.data and len(markets_response.data) > 0:
            return WrappedApiResponse(
                data=markets_response.data[0], status_code=markets_response.status_code
            )
        return WrappedApiResponse(data=None, status_code=markets_response.status_code)

    async def get_orderbook_snapshot(
        self, market_name: str
    ) -> WrappedApiResponse[OrderbookUpdateModel]:
        """
        Получить снимок ордербука через REST API.

        Args:
            market_name: Название рынка

        Returns:
            WrappedApiResponse[OrderbookUpdateModel]: Снимок ордербука
        """
        return await self._client.markets_info.get_orderbook_snapshot(market_name=market_name)

    async def subscribe_orderbook(
        self, market_name: str, start: bool = True, depth: Optional[int] = None
    ) -> OrderBook:
        """
        Подписаться на обновления ордербука через WebSocket.

        Args:
            market_name: Название рынка
            start: Запустить подписку сразу
            depth: Глубина ордербука (опционально)

        Returns:
            OrderBook: Объект ордербука для подписки на обновления
        """
        orderbook = await OrderBook.create(
            endpoint_config=self._config,
            market_name=market_name,
            start=start,
            depth=depth,
        )
        self._orderbooks[market_name] = orderbook
        return orderbook

    def get_best_bid_ask(
        self, market_name: str
    ) -> tuple[Optional[OrderBookEntry], Optional[OrderBookEntry]]:
        """
        Получить лучшие цены bid/ask из активного ордербука.

        Args:
            market_name: Название рынка

        Returns:
            tuple: (best_bid, best_ask) или (None, None) если ордербук не подписан
        """
        orderbook = self._orderbooks.get(market_name)
        if orderbook is None:
            return (None, None)

        best_bid = orderbook.best_bid()
        best_ask = orderbook.best_ask()
        return (best_bid, best_ask)

    async def close_orderbook(self, market_name: str) -> None:
        """
        Закрыть подписку на ордербук для указанного рынка.

        Args:
            market_name: Название рынка
        """
        orderbook = self._orderbooks.get(market_name)
        if orderbook:
            await orderbook.close()
            del self._orderbooks[market_name]

    async def close_all_orderbooks(self) -> None:
        """Закрыть все активные подписки на ордербуки."""
        for market_name in list(self._orderbooks.keys()):
            await self.close_orderbook(market_name)
