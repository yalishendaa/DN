"""Нормализованные модели данных для единого интерфейса адаптеров.

Все значения приведены к float (цены в USD, объёмы в базовом активе).
Это общий «язык» между адаптерами и контроллером.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PositionDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


# ---------------------------------------------------------------------------
# Normalized data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedBalance:
    """Баланс на бирже."""

    equity: float  # Общий капитал (включая PnL)
    available: float  # Доступно для торговли
    currency: str = "USD"  # Валюта баланса


@dataclass(frozen=True)
class NormalizedPosition:
    """Позиция по инструменту."""

    instrument: str  # Логический символ (напр. BTC-PERP)
    size: float  # Размер в базовом активе (+long, -short, 0=flat)
    direction: PositionDirection
    entry_price: float = 0.0  # Средняя цена входа (если доступна)
    mark_price: float = 0.0  # Текущая mark/ref цена
    unrealised_pnl: float = 0.0

    @property
    def notional(self) -> float:
        """Номинал позиции = |size| * mark_price."""
        return abs(self.size) * self.mark_price if self.mark_price else 0.0


@dataclass(frozen=True)
class NormalizedOrder:
    """Открытый ордер на бирже."""

    id: str  # Уникальный идентификатор для отмены
    instrument: str  # Логический символ
    side: Side
    price: float
    amount: float  # Полный объём ордера
    filled: float = 0.0  # Уже исполненный объём
    post_only: bool = False
    reduce_only: bool = False

    @property
    def remaining(self) -> float:
        return self.amount - self.filled


@dataclass(frozen=True)
class PlacedOrderResult:
    """Результат выставления ордера."""

    id: str  # Идентификатор ордера на бирже
    success: bool
    error: str | None = None


@dataclass
class ExchangeState:
    """Полное состояние одной биржи по одному инструменту."""

    exchange: str  # "extended", "nado" или "variational"
    instrument: str
    balance: NormalizedBalance = field(default_factory=lambda: NormalizedBalance(0.0, 0.0))
    position: NormalizedPosition = field(
        default_factory=lambda: NormalizedPosition("", 0.0, PositionDirection.FLAT)
    )
    open_orders: list[NormalizedOrder] = field(default_factory=list)
    reference_price: float = 0.0
    timestamp: float = 0.0  # UNIX timestamp сбора


@dataclass
class DeltaSnapshot:
    """Снимок дельты по одному инструменту."""

    instrument: str
    extended_state: ExchangeState
    nado_state: ExchangeState

    @property
    def extended_position(self) -> float:
        return self.extended_state.position.size

    @property
    def nado_position(self) -> float:
        return self.nado_state.position.size

    @property
    def net_delta(self) -> float:
        """Чистая дельта: сумма позиций обеих бирж."""
        return self.extended_position + self.nado_position

    @property
    def net_delta_usd(self) -> float:
        """Чистая дельта в USD (по средней ref-цене)."""
        ref = (self.extended_state.reference_price + self.nado_state.reference_price) / 2
        if ref == 0:
            return 0.0
        return self.net_delta * ref

    @property
    def mid_reference_price(self) -> float:
        """Средняя референсная цена между биржами."""
        p1 = self.extended_state.reference_price
        p2 = self.nado_state.reference_price
        if p1 > 0 and p2 > 0:
            return (p1 + p2) / 2
        return p1 or p2
