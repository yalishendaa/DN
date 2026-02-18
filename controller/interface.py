"""Абстрактный интерфейс адаптера биржи (контракт).

Оба адаптера (Extended, Nado) реализуют этот интерфейс.
Контроллер работает только через него — не знает деталей бирж.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from controller.models import (
    NormalizedBalance,
    NormalizedOrder,
    NormalizedPosition,
    PlacedOrderResult,
    Side,
)


class ExchangeAdapter(ABC):
    """Единый интерфейс для работы с биржей."""

    # -- Идентификация -------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Имя биржи (напр. 'extended', 'nado', 'variational')."""

    # -- Данные аккаунта -----------------------------------------------------

    @abstractmethod
    async def get_balance(self) -> NormalizedBalance:
        """Получить баланс (equity, available)."""

    @abstractmethod
    async def get_position(self, instrument: str) -> NormalizedPosition:
        """Получить текущую позицию по инструменту."""

    @abstractmethod
    async def get_open_orders(self, instrument: str) -> list[NormalizedOrder]:
        """Получить список открытых ордеров по инструменту."""

    # -- Референсная цена ----------------------------------------------------

    @abstractmethod
    async def get_reference_price(self, instrument: str) -> float:
        """Получить референсную цену (mid / mark / last)."""

    @abstractmethod
    async def get_best_bid_ask(self, instrument: str) -> tuple[float, float]:
        """Получить лучший bid/ask (0.0, 0.0 если недоступно)."""

    # -- Управление ордерами -------------------------------------------------

    @abstractmethod
    async def place_limit_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        post_only: bool = True,
        reduce_only: bool = False,
        external_id: str | None = None,
    ) -> PlacedOrderResult:
        """Выставить лимитный ордер.

        Args:
            instrument: Логический символ (напр. BTC-PERP).
            side: BUY или SELL.
            price: Цена в USD.
            amount: Объём в базовом активе (всегда > 0).
            post_only: Только maker.
            reduce_only: Только уменьшение позиции.
            external_id: Внешний ID для отслеживания (если биржа поддерживает).

        Returns:
            PlacedOrderResult с id ордера и статусом.
        """

    @abstractmethod
    async def cancel_order(self, instrument: str, order_id: str) -> bool:
        """Отменить ордер по ID. Возвращает True если отменён."""

    @abstractmethod
    async def cancel_all_orders(self, instrument: str) -> int:
        """Отменить все ордера по инструменту. Возвращает кол-во отменённых."""

    # -- Жизненный цикл ------------------------------------------------------

    @abstractmethod
    async def initialize(self) -> None:
        """Инициализация адаптера (подключение, кэши и т.д.)."""

    @abstractmethod
    async def close(self) -> None:
        """Корректное закрытие соединений."""
