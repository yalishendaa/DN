"""Модуль управления WebSocket подключениями для получения данных в реальном времени."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import List, Optional

from x10.perpetual.accounts import (
    AccountStreamDataModel,
    BalanceModel,
    OpenOrderModel,
    PositionModel,
)
from x10.perpetual.configuration import EndpointConfig
from x10.perpetual.stream_client.stream_client import PerpetualStreamClient
from x10.utils.http import WrappedStreamResponse

logger = logging.getLogger(__name__)

# Типы для callback'ов
BalanceCallback = Callable[[BalanceModel], Awaitable[None]]
PositionsCallback = Callable[[List[PositionModel]], Awaitable[None]]
OrdersCallback = Callable[[List[OpenOrderModel]], Awaitable[None]]


@dataclass
class WebSocketCache:
    """Кэш данных из WebSocket."""

    balance: Optional[BalanceModel] = None
    positions: List[PositionModel] = field(default_factory=list)
    orders: List[OpenOrderModel] = field(default_factory=list)
    last_update_time: dict[str, float] = field(default_factory=dict)
    messages_received: int = 0  # Счетчик полученных сообщений


class WebSocketManager:
    """Менеджер для управления WebSocket подключениями и кэшированием данных."""

    def __init__(self, endpoint_config: EndpointConfig, api_key: str):
        """
        Инициализация менеджера WebSocket.

        Args:
            endpoint_config: Конфигурация эндпоинта
            api_key: API ключ для аутентификации
        """
        self._config = endpoint_config
        self._api_key = api_key
        self._stream_client = PerpetualStreamClient(api_url=endpoint_config.stream_url)
        self._cache = WebSocketCache()
        self._connection_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._reconnect_delay = 5  # секунд
        self._connection_start_time: Optional[float] = None

        # Callback'и для уведомлений об обновлениях
        self._balance_callbacks: List[BalanceCallback] = []
        self._positions_callbacks: List[PositionsCallback] = []
        self._orders_callbacks: List[OrdersCallback] = []

    async def start(self) -> None:
        """
        Запустить WebSocket подключение.

        Если подключение уже запущено, ничего не делает.
        """
        if self._is_running:
            logger.warning("WebSocket уже запущен")
            return

        self._is_running = True
        self._connection_start_time = asyncio.get_event_loop().time()
        self._cache.messages_received = 0  # Сброс счетчика при запуске
        self._connection_task = asyncio.create_task(self._run_connection_loop())
        logger.info("WebSocket подключение запущено")

    async def stop(self) -> None:
        """Остановить WebSocket подключение."""
        self._is_running = False
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
            self._connection_task = None
        logger.info("WebSocket подключение остановлено")

    @property
    def is_running(self) -> bool:
        """Проверить, запущен ли WebSocket."""
        return self._is_running

    def get_cached_balance(self) -> Optional[BalanceModel]:
        """Получить кэшированный баланс."""
        return self._cache.balance

    def get_cached_positions(self) -> List[PositionModel]:
        """Получить кэшированные позиции."""
        return self._cache.positions.copy()

    def get_cached_orders(self) -> List[OpenOrderModel]:
        """Получить кэшированные ордера."""
        return self._cache.orders.copy()

    def get_statistics(self) -> dict:
        """
        Получить статистику работы WebSocket.

        Returns:
            dict: Словарь со статистикой (messages_received, last_updates, uptime и т.д.)
        """
        import time

        stats = {
            "is_running": self._is_running,
            "messages_received": self._cache.messages_received,
            "last_updates": self._cache.last_update_time.copy(),
            "has_balance": self._cache.balance is not None,
            "positions_count": len(self._cache.positions),
            "orders_count": len(self._cache.orders),
        }

        if self._connection_start_time:
            stats["uptime_seconds"] = time.time() - self._connection_start_time

        return stats

    def get_last_update_time(self, data_type: str) -> Optional[float]:
        """
        Получить время последнего обновления для указанного типа данных.

        Args:
            data_type: Тип данных ('balance', 'positions', 'orders')

        Returns:
            Optional[float]: Время последнего обновления или None
        """
        return self._cache.last_update_time.get(data_type)

    def subscribe_to_balance_updates(self, callback: BalanceCallback) -> None:
        """
        Подписаться на обновления баланса.

        Args:
            callback: Асинхронная функция, которая будет вызвана при обновлении баланса
        """
        self._balance_callbacks.append(callback)
        logger.debug(
            f"Добавлен callback для обновлений баланса. Всего: {len(self._balance_callbacks)}"
        )

    def subscribe_to_positions_updates(self, callback: PositionsCallback) -> None:
        """
        Подписаться на обновления позиций.

        Args:
            callback: Асинхронная функция, которая будет вызвана при обновлении позиций
        """
        self._positions_callbacks.append(callback)
        logger.debug(
            f"Добавлен callback для обновлений позиций. Всего: {len(self._positions_callbacks)}"
        )

    def subscribe_to_orders_updates(self, callback: OrdersCallback) -> None:
        """
        Подписаться на обновления ордеров.

        Args:
            callback: Асинхронная функция, которая будет вызвана при обновлении ордеров
        """
        self._orders_callbacks.append(callback)
        logger.debug(
            f"Добавлен callback для обновлений ордеров. Всего: {len(self._orders_callbacks)}"
        )

    async def _run_connection_loop(self) -> None:
        """Основной цикл подключения с автоматическим переподключением."""
        while self._is_running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                logger.info("WebSocket подключение отменено")
                break
            except Exception as e:
                logger.error(f"Ошибка в WebSocket подключении: {e}", exc_info=True)
                if self._is_running:
                    logger.info(f"Переподключение через {self._reconnect_delay} секунд...")
                    await asyncio.sleep(self._reconnect_delay)

    async def _connect_and_listen(self) -> None:
        """Подключиться к WebSocket и слушать обновления."""
        async with self._stream_client.subscribe_to_account_updates(
            self._api_key
        ) as account_stream:
            logger.info("✅ Подключено к account_updates stream")
            async for event in account_stream:
                if not self._is_running:
                    break
                # Увеличиваем счетчик полученных сообщений
                self._cache.messages_received += 1
                # Логируем каждое полученное сообщение
                logger.info(
                    f"📨 WebSocket: Получено сообщение #{self._cache.messages_received} "
                    f"(тип: {event.type}, seq: {event.seq})"
                )
                await self._handle_stream_event(event)

    async def _handle_stream_event(
        self, event: WrappedStreamResponse[AccountStreamDataModel]
    ) -> None:
        """
        Обработать событие из WebSocket stream.

        Args:
            event: Событие из WebSocket
        """
        if not event.data:
            return

        data = event.data

        # Обновление баланса
        if data.balance is not None:
            self._cache.balance = data.balance
            self._cache.last_update_time["balance"] = asyncio.get_event_loop().time()
            logger.info(
                f"💰 WebSocket: Баланс обновлен - {data.balance.balance} {data.balance.collateral_name} "
                f"(доступно: {data.balance.available_for_trade})"
            )
            # Вызвать все callback'и
            for callback in self._balance_callbacks:
                try:
                    await callback(data.balance)
                except Exception as e:
                    logger.error(f"Ошибка в callback баланса: {e}", exc_info=True)

        # Обновление позиций
        if data.positions is not None:
            self._cache.positions = data.positions
            self._cache.last_update_time["positions"] = asyncio.get_event_loop().time()
            logger.info(f"📊 WebSocket: Позиции обновлены - {len(data.positions)} позиций")
            # Вызвать все callback'и
            for callback in self._positions_callbacks:
                try:
                    await callback(data.positions)
                except Exception as e:
                    logger.error(f"Ошибка в callback позиций: {e}", exc_info=True)

        # Обновление ордеров
        if data.orders is not None:
            self._cache.orders = data.orders
            self._cache.last_update_time["orders"] = asyncio.get_event_loop().time()
            logger.info(f"📋 WebSocket: Ордера обновлены - {len(data.orders)} ордеров")
            # Вызвать все callback'и
            for callback in self._orders_callbacks:
                try:
                    await callback(data.orders)
                except Exception as e:
                    logger.error(f"Ошибка в callback ордеров: {e}", exc_info=True)

        # Обновление сделок (trades) - можно использовать для логирования
        if data.trades is not None and len(data.trades) > 0:
            logger.info(f"💹 WebSocket: Получены сделки - {len(data.trades)} сделок")
