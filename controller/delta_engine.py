"""Движок расчёта дельты и генерации решений по выравниванию.

Чистая логика без побочных эффектов: принимает состояние, возвращает действия.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from controller.config import ControllerConfig
from controller.models import (
    DeltaSnapshot,
    Side,
)

logger = logging.getLogger("dn.engine")


# ---------------------------------------------------------------------------
# Action models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RebalanceAction:
    """Одно действие по выравниванию дельты."""

    exchange: str  # "extended", "nado" или "variational"
    instrument: str
    side: Side
    amount: float  # Объём в базовом активе (> 0)
    price: float  # Целевая цена
    reason: str  # Человекочитаемое обоснование


@dataclass
class DeltaDecision:
    """Результат анализа дельты по одному инструменту."""

    instrument: str
    net_delta: float  # Чистая дельта (сумма позиций)
    net_delta_usd: float  # Чистая дельта в USD
    within_tolerance: bool  # В пределах допуска?
    actions: list[RebalanceAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Delta engine
# ---------------------------------------------------------------------------


class DeltaEngine:
    """Рассчитывает текущую дельту и генерирует действия по выравниванию."""

    def __init__(self, config: ControllerConfig):
        self._config = config
        self._risk = config.risk

    def analyze(self, snapshot: DeltaSnapshot) -> DeltaDecision:
        """Анализировать снимок и решить, нужно ли выравнивание.

        Args:
            snapshot: Текущее состояние обеих бирж по одному инструменту.

        Returns:
            DeltaDecision с расчётами и (опционально) действиями.
        """
        instrument = snapshot.instrument
        delta = snapshot.net_delta
        delta_usd = snapshot.net_delta_usd
        ref_price = snapshot.mid_reference_price

        # Проверяем допуски
        within_base = abs(delta) <= self._risk.max_delta_base
        within_usd = abs(delta_usd) <= self._risk.max_delta_usd
        within_tolerance = within_base and within_usd

        decision = DeltaDecision(
            instrument=instrument,
            net_delta=delta,
            net_delta_usd=delta_usd,
            within_tolerance=within_tolerance,
        )

        # Проверки безопасности
        self._check_safety(snapshot, decision)

        # Если в допуске или только мониторинг — возвращаем без действий
        if within_tolerance or self._config.mode == "monitor":
            return decision

        # Генерируем действия по выравниванию
        actions = self._generate_rebalance_actions(snapshot, delta, ref_price)
        decision.actions = actions

        return decision

    def _check_safety(self, snapshot: DeltaSnapshot, decision: DeltaDecision) -> None:
        """Проверки безопасности: балансы, доступность данных."""
        ext = snapshot.extended_state
        nado = snapshot.nado_state
        ext_name = ext.exchange.capitalize()
        nado_name = nado.exchange.capitalize()

        # Проверяем наличие ref-цены
        if ext.reference_price <= 0:
            decision.warnings.append(f"{ext_name}: ref price = 0 для {snapshot.instrument}")
        if nado.reference_price <= 0:
            decision.warnings.append(f"{nado_name}: ref price = 0 для {snapshot.instrument}")

        # Проверяем минимальный баланс
        if ext.balance.available < self._risk.min_balance_usd:
            decision.warnings.append(
                f"{ext_name}: баланс {ext.balance.available:.2f} < мин. {self._risk.min_balance_usd}"
            )
        if nado.balance.available < self._risk.min_balance_usd:
            decision.warnings.append(
                f"{nado_name}: баланс {nado.balance.available:.2f} < мин. {self._risk.min_balance_usd}"
            )

        # Проверяем расхождение цен между биржами
        if ext.reference_price > 0 and nado.reference_price > 0:
            spread = abs(ext.reference_price - nado.reference_price)
            spread_pct = spread / min(ext.reference_price, nado.reference_price) * 100
            if spread_pct > 1.0:
                decision.warnings.append(
                    f"Расхождение цен: {spread_pct:.3f}% "
                    f"({ext_name}={ext.reference_price:.2f}, {nado_name}={nado.reference_price:.2f})"
                )

    def _generate_rebalance_actions(
        self,
        snapshot: DeltaSnapshot,
        delta: float,
        ref_price: float,
    ) -> list[RebalanceAction]:
        """Генерация действий для возврата дельты к 0.

        Стратегия: уменьшаем позицию на стороне с большей экспозицией,
        при необходимости увеличиваем на противоположной.
        """
        if ref_price <= 0:
            logger.warning("Ref price = 0, невозможно рассчитать ордера")
            return []

        actions: list[RebalanceAction] = []
        instrument = snapshot.instrument

        # delta > 0: суммарно long → нужно sell/сократить
        # delta < 0: суммарно short → нужно buy/увеличить
        rebalance_amount = min(abs(delta), self._risk.max_order_size_base)

        if rebalance_amount <= 0:
            return []

        # Определяем, на какой бирже действовать
        ext_pos = snapshot.extended_position
        nado_pos = snapshot.nado_position
        ext_exchange = snapshot.extended_state.exchange
        nado_exchange = snapshot.nado_state.exchange

        offset = ref_price * self._config.price_offset_pct / 100

        if delta > 0:
            # Нужно sell для уменьшения дельты
            # Продаём на бирже с большей long-позицией
            if ext_pos >= nado_pos:
                # Extended имеет больше long → продаём там
                actions.append(
                    RebalanceAction(
                        exchange=ext_exchange,
                        instrument=instrument,
                        side=Side.SELL,
                        amount=rebalance_amount,
                        price=ref_price - offset,  # чуть ниже mid для maker
                        reason=f"Reduce delta: sell on {ext_exchange} (pos={ext_pos:.6f})",
                    )
                )
            else:
                # Nado имеет больше long → продаём там
                actions.append(
                    RebalanceAction(
                        exchange=nado_exchange,
                        instrument=instrument,
                        side=Side.SELL,
                        amount=rebalance_amount,
                        price=ref_price - offset,
                        reason=f"Reduce delta: sell on {nado_exchange} (pos={nado_pos:.6f})",
                    )
                )
        else:
            # Нужно buy для уменьшения дельты
            if ext_pos <= nado_pos:
                # Extended имеет больше short → покупаем там
                actions.append(
                    RebalanceAction(
                        exchange=ext_exchange,
                        instrument=instrument,
                        side=Side.BUY,
                        amount=rebalance_amount,
                        price=ref_price + offset,
                        reason=f"Reduce delta: buy on {ext_exchange} (pos={ext_pos:.6f})",
                    )
                )
            else:
                actions.append(
                    RebalanceAction(
                        exchange=nado_exchange,
                        instrument=instrument,
                        side=Side.BUY,
                        amount=rebalance_amount,
                        price=ref_price + offset,
                        reason=f"Reduce delta: buy on {nado_exchange} (pos={nado_pos:.6f})",
                    )
                )

        # Валидация: не превышаем лимит позиции
        actions = self._validate_actions(actions, snapshot)

        return actions

    def _validate_actions(
        self,
        actions: list[RebalanceAction],
        snapshot: DeltaSnapshot,
    ) -> list[RebalanceAction]:
        """Отфильтровать действия, которые нарушают лимиты."""
        validated = []
        for action in actions:
            # Проверяем, не превысим ли макс. позицию
            if action.exchange == snapshot.extended_state.exchange:
                current = abs(snapshot.extended_position)
            elif action.exchange == snapshot.nado_state.exchange:
                current = abs(snapshot.nado_position)
            else:
                logger.warning(
                    "Action skipped: exchange '%s' not found in current snapshot",
                    action.exchange,
                )
                continue

            if current + action.amount > self._risk.max_position_base:
                logger.warning(
                    "Action skipped: %s on %s would exceed max_position_base (%.4f + %.4f > %.4f)",
                    action.side.value,
                    action.exchange,
                    current,
                    action.amount,
                    self._risk.max_position_base,
                )
                continue
            validated.append(action)
        return validated
