"""Основной класс торгового бота для Extended Exchange."""

from typing import Optional

from bot.account import AccountManager
from bot.client import create_trading_client
from bot.config import ExtendedBotConfig
from bot.markets import MarketsManager
from bot.orders import OrdersManager
from bot.websocket_manager import WebSocketManager
from x10.perpetual.trading_client import PerpetualTradingClient


class ExtendedTradingBot:
    """Основной класс торгового бота для Extended Exchange."""

    def __init__(self, config: ExtendedBotConfig):
        """
        Инициализация торгового бота.

        Args:
            config: Конфигурация бота с API ключами и настройками
        """
        self._config = config
        self._client = create_trading_client(config)
        # Инициализация WebSocket менеджера (не запускается автоматически)
        self._websocket_manager: Optional[WebSocketManager] = WebSocketManager(
            endpoint_config=config.endpoint_config, api_key=config.api_key
        )
        # Передаем WebSocket менеджер в AccountManager
        self._account_manager = AccountManager(
            self._client, websocket_manager=self._websocket_manager
        )
        self._markets_manager = MarketsManager(self._client, config.endpoint_config)
        self._orders_manager = OrdersManager(self._client)

    @property
    def account(self) -> AccountManager:
        """Получить менеджер аккаунта."""
        return self._account_manager

    @property
    def markets(self) -> MarketsManager:
        """Получить менеджер рынков."""
        return self._markets_manager

    @property
    def orders(self) -> OrdersManager:
        """Получить менеджер ордеров."""
        return self._orders_manager

    @property
    def client(self) -> PerpetualTradingClient:
        """Получить торговый клиент напрямую (для расширенного использования)."""
        return self._client

    @property
    def websocket(self) -> Optional[WebSocketManager]:
        """Получить менеджер WebSocket."""
        return self._websocket_manager

    def get_websocket_status(self) -> dict:
        """
        Получить статус WebSocket подключения.

        Returns:
            dict: Словарь со статистикой WebSocket или пустой dict, если WebSocket не инициализирован
        """
        if self._websocket_manager:
            return self._websocket_manager.get_statistics()
        return {"is_running": False, "error": "WebSocket не инициализирован"}

    async def start_websocket(self) -> None:
        """
        Запустить WebSocket подключение для получения данных в реальном времени.

        После запуска данные баланса, позиций и ордеров будут обновляться автоматически
        через WebSocket, что ускорит работу бота и снизит нагрузку на REST API.
        """
        if self._websocket_manager:
            await self._websocket_manager.start()

    async def stop_websocket(self) -> None:
        """Остановить WebSocket подключение."""
        if self._websocket_manager:
            await self._websocket_manager.stop()

    async def close(self) -> None:
        """Корректно закрыть все соединения и подписки."""
        # Останавливаем WebSocket, если запущен
        if self._websocket_manager:
            await self._websocket_manager.stop()
        await self._markets_manager.close_all_orderbooks()
        await self._client.close()
