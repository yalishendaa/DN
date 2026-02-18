"""Основной цикл Delta-Neutral контроллера.

Собирает состояние с обеих бирж, рассчитывает дельту,
принимает решения и (в auto-режиме) выставляет ордера.
"""

from __future__ import annotations

import asyncio
import logging
import time

from controller.config import ControllerConfig, ExchangeName
from controller.delta_engine import DeltaEngine, DeltaDecision, RebalanceAction
from controller.extended_adapter import ExtendedAdapter
from controller.interface import ExchangeAdapter
from controller.models import DeltaSnapshot, ExchangeState
from controller.nado_adapter import NadoAdapter
from controller.variational_adapter import VariationalAdapter

logger = logging.getLogger("dn.controller")


class DeltaNeutralController:
    """Контроллер дельта-нейтральной стратегии."""

    def __init__(self, config: ControllerConfig):
        self._config = config
        self._engine = DeltaEngine(config)
        self._running = False
        self._cycle_count = 0

        # Активная торговая пара из конфига
        self._primary_exchange = config.entry_primary_exchange
        self._secondary_exchange = config.entry_secondary_exchange

        # Инициализированные адаптеры по имени биржи
        self._adapters: dict[str, ExchangeAdapter] = {}

    # -- Инициализация -------------------------------------------------------

    def _build_adapter(self, exchange: ExchangeName) -> ExchangeAdapter:
        if exchange == "extended":
            ext_map: dict[str, str] = {}
            for inst in self._config.instruments:
                if not inst.extended_market_name:
                    raise ValueError(
                        "Для Extended в runtime-контроллере нужен "
                        f"instruments[].extended_market_name (symbol={inst.symbol})"
                    )
                ext_map[inst.symbol] = inst.extended_market_name
            return ExtendedAdapter(
                env_file=self._config.extended_env_file,
                instrument_map=ext_map,
            )

        if exchange == "nado":
            nado_map: dict[str, int] = {}
            for inst in self._config.instruments:
                if inst.nado_product_id is None:
                    raise ValueError(
                        "Для Nado в runtime-контроллере нужен "
                        f"instruments[].nado_product_id (symbol={inst.symbol})"
                    )
                nado_map[inst.symbol] = inst.nado_product_id
            return NadoAdapter(
                env_file=self._config.nado_env_file,
                instrument_map=nado_map,
                network=self._config.nado_network,
                subaccount_name=self._config.nado_subaccount_name,
            )

        if exchange == "variational":
            variational_map: dict[str, str] = {}
            for inst in self._config.instruments:
                if not inst.variational_underlying:
                    raise ValueError(
                        "Для Variational в runtime-контроллере нужен "
                        f"instruments[].variational_underlying (symbol={inst.symbol})"
                    )
                variational_map[inst.symbol] = inst.variational_underlying
            return VariationalAdapter(
                env_file=self._config.variational_env_file or "Variational/.env",
                instrument_map=variational_map,
            )

        raise ValueError(f"Неподдерживаемая биржа: {exchange}")

    async def initialize(self) -> None:
        """Создать и инициализировать адаптеры активной пары из конфига."""
        logger.info("=" * 60)
        logger.info("Delta-Neutral Controller — инициализация")
        logger.info("Режим: %s", self._config.mode.upper())
        logger.info("Инструменты: %s", [i.symbol for i in self._config.instruments])
        logger.info(
            "Пара: %s + %s",
            self._primary_exchange,
            self._secondary_exchange,
        )
        logger.info("=" * 60)

        self._adapters.clear()
        for exchange in (self._primary_exchange, self._secondary_exchange):
            if exchange in self._adapters:
                continue
            adapter = self._build_adapter(exchange)
            await adapter.initialize()
            self._adapters[exchange] = adapter

        logger.info("Инициализировано адаптеров: %s", sorted(self._adapters.keys()))

    async def close(self) -> None:
        """Корректно закрыть все инициализированные адаптеры."""
        self._running = False
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception as e:
                logger.warning("Ошибка закрытия адаптера %s: %s", adapter.name, e)
        logger.info("Контроллер остановлен")

    # -- Сбор состояния ------------------------------------------------------

    async def _collect_state(
        self,
        adapter: ExchangeAdapter,
        instrument: str,
    ) -> ExchangeState:
        """Собрать полное состояние одной биржи по одному инструменту."""
        ts = time.time()
        state = ExchangeState(
            exchange=adapter.name,
            instrument=instrument,
            timestamp=ts,
        )

        try:
            # Параллельный сбор данных
            balance_task = asyncio.create_task(adapter.get_balance())
            position_task = asyncio.create_task(adapter.get_position(instrument))
            orders_task = asyncio.create_task(adapter.get_open_orders(instrument))
            ref_price_task = asyncio.create_task(adapter.get_reference_price(instrument))

            results = await asyncio.gather(
                balance_task,
                position_task,
                orders_task,
                ref_price_task,
                return_exceptions=True,
            )

            # Обрабатываем результаты
            if not isinstance(results[0], Exception):
                state.balance = results[0]
            else:
                logger.error("%s: ошибка получения баланса: %s", adapter.name, results[0])

            if not isinstance(results[1], Exception):
                state.position = results[1]
            else:
                logger.error("%s: ошибка получения позиции: %s", adapter.name, results[1])

            if not isinstance(results[2], Exception):
                state.open_orders = results[2]
            else:
                logger.error("%s: ошибка получения ордеров: %s", adapter.name, results[2])

            if not isinstance(results[3], Exception):
                state.reference_price = results[3]
            else:
                logger.error("%s: ошибка получения ref price: %s", adapter.name, results[3])

        except Exception as e:
            logger.error("%s: критическая ошибка сбора состояния: %s", adapter.name, e)

        return state

    # -- Исполнение действий -------------------------------------------------

    async def _execute_action(self, action: RebalanceAction) -> bool:
        """Исполнить одно действие по выравниванию."""
        adapter = self._adapters.get(action.exchange)
        if adapter is None:
            logger.error("Адаптер %s не инициализирован", action.exchange)
            return False

        logger.info(
            "EXECUTE: %s %s %.6f %s @ %.2f на %s | %s",
            action.side.value.upper(),
            action.amount,
            action.amount,
            action.instrument,
            action.price,
            action.exchange.upper(),
            action.reason,
        )

        result = await adapter.place_limit_order(
            instrument=action.instrument,
            side=action.side,
            price=action.price,
            amount=action.amount,
            post_only=self._config.order_post_only,
        )

        if result.success:
            logger.info("  → Ордер выставлен: %s", result.id)
        else:
            logger.error("  → Ошибка: %s", result.error)

        return result.success

    # -- Один цикл -----------------------------------------------------------

    async def _run_cycle(self) -> None:
        """Один цикл: сбор → расчёт → решение → (исполнение)."""
        self._cycle_count += 1
        cycle_start = time.time()

        logger.info("─" * 50)
        logger.info("Цикл #%d", self._cycle_count)
        primary_adapter = self._adapters.get(self._primary_exchange)
        secondary_adapter = self._adapters.get(self._secondary_exchange)
        if primary_adapter is None or secondary_adapter is None:
            raise RuntimeError(
                "Активные адаптеры не инициализированы: "
                f"{self._primary_exchange}, {self._secondary_exchange}"
            )

        for inst_cfg in self._config.instruments:
            instrument = inst_cfg.symbol

            # 1. Сбор состояния с обеих бирж (параллельно)
            ext_state, nado_state = await asyncio.gather(
                self._collect_state(primary_adapter, instrument),
                self._collect_state(secondary_adapter, instrument),
            )

            # 2. Формируем снимок
            snapshot = DeltaSnapshot(
                instrument=instrument,
                extended_state=ext_state,
                nado_state=nado_state,
            )

            # 3. Анализ дельты
            decision = self._engine.analyze(snapshot)

            # 4. Вывод результатов
            self._log_decision(snapshot, decision)

            # 5. Исполнение (только в auto-режиме)
            if decision.actions and self._config.mode == "auto":
                # Проверяем: обе биржи доступны?
                if ext_state.reference_price <= 0 or nado_state.reference_price <= 0:
                    logger.warning(
                        "SKIP execution: одна из бирж недоступна "
                        "(%s_ref=%.2f, %s_ref=%.2f)",
                        ext_state.exchange,
                        ext_state.reference_price,
                        nado_state.exchange,
                        nado_state.reference_price,
                    )
                    continue

                for action in decision.actions:
                    success = await self._execute_action(action)
                    if not success:
                        logger.warning("Действие не удалось, пропускаем остальные")
                        break

        elapsed = time.time() - cycle_start
        logger.info("Цикл #%d завершён за %.2f сек", self._cycle_count, elapsed)

    def _log_decision(self, snapshot: DeltaSnapshot, decision: DeltaDecision) -> None:
        """Вывести результаты анализа в лог."""
        ext = snapshot.extended_state
        nado = snapshot.nado_state

        status = "OK" if decision.within_tolerance else "IMBALANCE"
        logger.info(
            "[%s] %s | delta=%.6f (%.2f USD) | "
            "%s_pos=%.6f %s_pos=%.6f | "
            "%s_ref=%.2f %s_ref=%.2f | "
            "%s_bal=%.2f %s_bal=%.2f | "
            "%s_orders=%d %s_orders=%d",
            status,
            decision.instrument,
            decision.net_delta,
            decision.net_delta_usd,
            ext.exchange,
            snapshot.extended_position,
            nado.exchange,
            snapshot.nado_position,
            ext.exchange,
            ext.reference_price,
            nado.exchange,
            nado.reference_price,
            ext.exchange,
            ext.balance.equity,
            nado.exchange,
            nado.balance.equity,
            ext.exchange,
            len(ext.open_orders),
            nado.exchange,
            len(nado.open_orders),
        )

        for warn in decision.warnings:
            logger.warning("  ⚠ %s", warn)

        if decision.actions:
            for action in decision.actions:
                logger.info(
                    "  → ACTION: %s %.6f %s @ %.2f on %s (%s)",
                    action.side.value.upper(),
                    action.amount,
                    action.instrument,
                    action.price,
                    action.exchange.upper(),
                    action.reason,
                )
        elif not decision.within_tolerance and self._config.mode == "monitor":
            logger.info("  → Режим monitor: действий не предпринимается")

    # -- Основной цикл -------------------------------------------------------

    async def run(self) -> None:
        """Запустить основной цикл контроллера."""
        self._running = True
        logger.info("Контроллер запущен (интервал %.1f сек)", self._config.cycle_interval_sec)

        try:
            while self._running:
                try:
                    await self._run_cycle()
                except Exception as e:
                    logger.error("Ошибка в цикле #%d: %s", self._cycle_count, e, exc_info=True)

                # Пауза между циклами
                await asyncio.sleep(self._config.cycle_interval_sec)
        except asyncio.CancelledError:
            logger.info("Контроллер отменён")
        finally:
            await self.close()

    def stop(self) -> None:
        """Остановить контроллер."""
        self._running = False
        logger.info("Запрошена остановка контроллера")
