#!/usr/bin/env python3
"""Скрипт верификации выставления/отмены ордеров через адаптеры.

Назначение
==========
- Убедиться, что запросы уходят на правильные эндпоинты (Extended / Nado / Variational).
- Убедиться, что place_limit_order / cancel_order работают как ожидается.
- Использование: после изменений в адаптерах, на тестнете перед auto-режимом,
  при отладке интеграции.

Режимы
======
--dry-run   Полностью офлайн-проверка: адаптеры не инициализируются,
            сетевые вызовы и ордера не выполняются.
            Скрипт валидирует конфиг и печатает, какие операции и endpoint
            были бы использованы.

--live      Реальное выставление тестового ордера (маленький объём, далеко от
            рынка, post_only=True) и, если не указан --no-cancel, его отмена
            с проверкой, что ордер исчез из get_open_orders.
            Требует одновременно: флаг --live и CONFIRM_LIVE_TRADING=1.

Флаги
=====
--exchange extended|nado|variational|both   Какую биржу проверять (по умолчанию extended).
--no-cancel                     Не отменять ордер после проверки (только --live).
--config PATH                   Путь к config.yaml контроллера.
--price-offset-pct PCT          Сдвиг цены от mark (по умолчанию 10%).
                                10% от mark = ордер далеко от рынка → не исполнится.
--test-amount AMOUNT            Объём тестового ордера в базовом активе (по умолчанию 0.002).

Как по логам убедиться, что «запросы идут куда надо»
====================================================
1. В каждой строке лога есть поле ``endpoint=...`` — это базовый URL API биржи.
   - Extended mainnet : ``https://api.starknet.extended.exchange/api/v1``
   - Extended testnet : ``https://api.starknet.sepolia.extended.exchange/api/v1``
   - Nado mainnet     : ``https://gateway.prod.nado.xyz/v1``
   - Nado testnet     : ``https://gateway.test.nado.xyz/v1``
   - Variational      : ``https://omni.variational.io/api``
2. Убедитесь, что endpoint соответствует ожидаемому окружению (mainnet/testnet).
3. Каждая операция (get_balance, place_limit_order, cancel_order, …) логируется
   с параметрами запроса и результатом (OK / FAIL + причина).
4. В конце выводится итоговая таблица PASS / FAIL по каждой бирже и операции.

Примеры запуска
===============
::

    # Dry-run (по умолчанию): полностью офлайн, без сетевых запросов
    python -m controller.scripts.verify_order_placement
    python -m controller.scripts.verify_order_placement --dry-run --exchange both

    # Live-проверка Extended с отменой (требуется safety gate)
    CONFIRM_LIVE_TRADING=1 python -m controller.scripts.verify_order_placement --live --exchange extended

    # Live-проверка Nado, ордер НЕ отменяется
    python -m controller.scripts.verify_order_placement --live --exchange nado --no-cancel

    # Dry-run Variational
    python -m controller.scripts.verify_order_placement --dry-run --exchange variational
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Ensure DN root is on sys.path so ``controller`` package is importable ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_CONTROLLER_DIR = _SCRIPT_DIR.parent
_DN_ROOT = _CONTROLLER_DIR.parent
if str(_DN_ROOT) not in sys.path:
    sys.path.insert(0, str(_DN_ROOT))

from controller.config import load_config, ControllerConfig
from controller.extended_adapter import ExtendedAdapter
from controller.interface import ExchangeAdapter
from controller.models import Side
from controller.nado_adapter import NadoAdapter
from controller.safety import LiveTradingSafetyError, require_live_confirmation
from controller.variational_adapter import VariationalAdapter

# ───────────────────────────────────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────────────────────────────────

_LOG_FMT = "%(asctime)s | %(levelname)-5s | %(message)s"
_DATE_FMT = "%H:%M:%S"

logger = logging.getLogger("verify")


def _setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
    # Подавляем шум SDK
    for name in (
        "nado_grid",
        "aiohttp",
        "websockets",
        "dn.extended",
        "dn.nado",
        "dn.variational",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


# ───────────────────────────────────────────────────────────────────────────
# Endpoint detection helpers
# ───────────────────────────────────────────────────────────────────────────


def _get_extended_endpoint(adapter: ExtendedAdapter) -> str:
    """Вернуть базовый API URL Extended (без секретов)."""
    try:
        cfg = adapter.bot._config  # ExtendedBotConfig
        return cfg.endpoint_config.api_base_url
    except Exception:
        return "<не удалось определить>"


def _get_nado_endpoint(adapter: NadoAdapter) -> str:
    """Вернуть базовый gateway URL Nado (без секретов)."""
    try:
        engine = adapter.client.client.context.engine_client
        return engine.url
    except Exception:
        return "<не удалось определить>"


def _get_endpoint(adapter: ExchangeAdapter) -> str:
    if isinstance(adapter, ExtendedAdapter):
        return _get_extended_endpoint(adapter)
    if isinstance(adapter, NadoAdapter):
        return _get_nado_endpoint(adapter)
    if isinstance(adapter, VariationalAdapter):
        return "https://omni.variational.io/api"
    return "<unknown>"


# ───────────────────────────────────────────────────────────────────────────
# Step result tracking
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class StepResult:
    exchange: str
    operation: str
    params: str
    endpoint: str
    ok: bool
    detail: str
    elapsed_ms: float = 0.0


@dataclass
class VerifyReport:
    exchange: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def add(self, step: StepResult) -> None:
        status = "OK" if step.ok else "FAIL"
        logger.info(
            "  [%s] %-24s | endpoint=%s | params=%s | %s (%.0f ms)",
            status,
            step.operation,
            step.endpoint,
            step.params,
            step.detail,
            step.elapsed_ms,
        )
        self.steps.append(step)


# ───────────────────────────────────────────────────────────────────────────
# Verification logic
# ───────────────────────────────────────────────────────────────────────────


async def _run_read_checks(
    adapter: ExchangeAdapter,
    instrument: str,
    report: VerifyReport,
) -> Optional[float]:
    """Выполнить read-операции, вернуть ref_price (или None при ошибке)."""
    endpoint = _get_endpoint(adapter)
    name = adapter.name.upper()

    # ── get_balance ─────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        bal = await adapter.get_balance()
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_balance",
                params="",
                endpoint=endpoint,
                ok=True,
                detail=f"equity={bal.equity:.2f} available={bal.available:.2f} {bal.currency}",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_balance",
                params="",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )

    # ── get_position ────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        pos = await adapter.get_position(instrument)
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_position",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=True,
                detail=f"size={pos.size:.6f} dir={pos.direction.value} mark={pos.mark_price:.2f}",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_position",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )

    # ── get_open_orders ─────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        orders = await adapter.get_open_orders(instrument)
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_open_orders",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=True,
                detail=f"count={len(orders)}",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_open_orders",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )

    # ── get_reference_price ─────────────────────────────────────────────
    ref_price: Optional[float] = None
    t0 = time.monotonic()
    try:
        ref_price = await adapter.get_reference_price(instrument)
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_reference_price",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=ref_price > 0,
                detail=f"ref_price={ref_price:.2f}" if ref_price else "ref_price=0 (warning)",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="get_reference_price",
                params=f"instrument={instrument}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )

    return ref_price


async def _run_dry_run(
    adapter: ExchangeAdapter,
    instrument: str,
    report: VerifyReport,
    test_amount: float,
    price_offset_pct: float,
) -> None:
    """Dry-run: read-операции + логирование того, что БЫЛО БЫ вызвано."""
    endpoint = _get_endpoint(adapter)
    name = adapter.name.upper()

    ref_price = await _run_read_checks(adapter, instrument, report)

    # ── Логируем, что place_limit_order СДЕЛАЛ БЫ ──────────────────────
    if ref_price and ref_price > 0:
        test_price = round(ref_price * (1 - price_offset_pct / 100), 2)
        params_str = (
            f"instrument={instrument} side=buy price={test_price} "
            f"amount={test_amount} post_only=True reduce_only=False"
        )
    else:
        test_price = 0.0
        params_str = (
            f"instrument={instrument} side=buy price=<unknown — no ref_price> amount={test_amount}"
        )

    report.add(
        StepResult(
            exchange=name,
            operation="place_limit_order [DRY-RUN]",
            params=params_str,
            endpoint=endpoint,
            ok=True,
            detail="NOT EXECUTED — dry-run mode",
            elapsed_ms=0,
        )
    )

    report.add(
        StepResult(
            exchange=name,
            operation="cancel_order [DRY-RUN]",
            params="order_id=<would-be-placed>",
            endpoint=endpoint,
            ok=True,
            detail="NOT EXECUTED — dry-run mode",
            elapsed_ms=0,
        )
    )


async def _run_live(
    adapter: ExchangeAdapter,
    instrument: str,
    report: VerifyReport,
    test_amount: float,
    price_offset_pct: float,
    no_cancel: bool,
) -> None:
    """Live: реальное выставление + (опционально) отмена ордера."""
    endpoint = _get_endpoint(adapter)
    name = adapter.name.upper()

    ref_price = await _run_read_checks(adapter, instrument, report)

    if not ref_price or ref_price <= 0:
        report.add(
            StepResult(
                exchange=name,
                operation="place_limit_order",
                params="",
                endpoint=endpoint,
                ok=False,
                detail="Невозможно: ref_price = 0, нельзя рассчитать тестовую цену",
                elapsed_ms=0,
            )
        )
        return

    # Тестовая цена: далеко от рынка (BUY ниже mark на offset %)
    test_price = round(ref_price * (1 - price_offset_pct / 100), 2)
    params_str = (
        f"instrument={instrument} side=buy price={test_price} "
        f"amount={test_amount} post_only=True reduce_only=False"
    )

    # ── place_limit_order ───────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        result = await adapter.place_limit_order(
            instrument=instrument,
            side=Side.BUY,
            price=test_price,
            amount=test_amount,
            post_only=True,
            reduce_only=False,
            external_id=f"verify-{int(time.time())}",
        )
        dt = (time.monotonic() - t0) * 1000
        if result.success:
            report.add(
                StepResult(
                    exchange=name,
                    operation="place_limit_order",
                    params=params_str,
                    endpoint=endpoint,
                    ok=True,
                    detail=f"order_id={result.id}",
                    elapsed_ms=dt,
                )
            )
        else:
            report.add(
                StepResult(
                    exchange=name,
                    operation="place_limit_order",
                    params=params_str,
                    endpoint=endpoint,
                    ok=False,
                    detail=f"error: {result.error}",
                    elapsed_ms=dt,
                )
            )
            return  # нечего отменять
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="place_limit_order",
                params=params_str,
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )
        return

    placed_id = result.id

    # ── Проверяем, что ордер виден в get_open_orders ────────────────────
    await asyncio.sleep(1)  # даём бирже время обработать
    t0 = time.monotonic()
    try:
        orders_after = await adapter.get_open_orders(instrument)
        dt = (time.monotonic() - t0) * 1000
        found = any(o.id == placed_id for o in orders_after)
        report.add(
            StepResult(
                exchange=name,
                operation="verify_order_visible",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=found,
                detail=f"found_in_open_orders={found} (total={len(orders_after)})",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="verify_order_visible",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )

    # ── cancel_order (если не --no-cancel) ──────────────────────────────
    if no_cancel:
        report.add(
            StepResult(
                exchange=name,
                operation="cancel_order [SKIPPED]",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=True,
                detail="skipped (--no-cancel)",
                elapsed_ms=0,
            )
        )
        return

    t0 = time.monotonic()
    try:
        cancelled = await adapter.cancel_order(instrument, placed_id)
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="cancel_order",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=cancelled,
                detail=f"cancelled={cancelled}",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="cancel_order",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )
        return

    # ── Проверяем, что ордер исчез из get_open_orders ───────────────────
    await asyncio.sleep(1)
    t0 = time.monotonic()
    try:
        orders_final = await adapter.get_open_orders(instrument)
        dt = (time.monotonic() - t0) * 1000
        still_there = any(o.id == placed_id for o in orders_final)
        report.add(
            StepResult(
                exchange=name,
                operation="verify_order_cancelled",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=not still_there,
                detail=f"still_in_open_orders={still_there} (total={len(orders_final)})",
                elapsed_ms=dt,
            )
        )
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        report.add(
            StepResult(
                exchange=name,
                operation="verify_order_cancelled",
                params=f"order_id={placed_id}",
                endpoint=endpoint,
                ok=False,
                detail=str(e),
                elapsed_ms=dt,
            )
        )


# ───────────────────────────────────────────────────────────────────────────
# Dry-run (offline) helpers
# ───────────────────────────────────────────────────────────────────────────


def _requested_exchange_names(exchange: str) -> list[str]:
    if exchange == "both":
        return ["extended", "nado"]
    return [exchange]


def _resolve_expected_endpoint(exchange: str, config: ControllerConfig) -> str:
    if exchange == "extended":
        if config.extended_network == "mainnet":
            return "https://api.starknet.extended.exchange/api/v1"
        return "https://api.starknet.sepolia.extended.exchange/api/v1"
    if exchange == "nado":
        if config.nado_network == "mainnet":
            return "https://gateway.prod.nado.xyz/v1"
        if config.nado_network == "testnet":
            return "https://gateway.test.nado.xyz/v1"
        return "<sdk-resolved devnet gateway>"
    if exchange == "variational":
        return "https://omni.variational.io/api"
    return "<unknown>"


def _has_instrument_mapping(config: ControllerConfig, exchange: str) -> bool:
    if exchange == "extended":
        return any(inst.extended_market_name for inst in config.instruments)
    if exchange == "nado":
        return any(inst.nado_product_id is not None for inst in config.instruments)
    if exchange == "variational":
        return any(inst.variational_underlying for inst in config.instruments)
    return False


def _run_offline_dry_run(
    config: ControllerConfig,
    exchange: str,
    instrument: str,
    test_amount: float,
    price_offset_pct: float,
) -> list[VerifyReport]:
    reports: list[VerifyReport] = []
    for exchange_name in _requested_exchange_names(exchange):
        report = VerifyReport(exchange=exchange_name)
        endpoint = _resolve_expected_endpoint(exchange_name, config)
        has_mapping = _has_instrument_mapping(config, exchange_name)

        report.add(
            StepResult(
                exchange=exchange_name.upper(),
                operation="initialize [DRY-RUN]",
                params="",
                endpoint=endpoint,
                ok=has_mapping,
                detail=(
                    "adapter initialization skipped"
                    if has_mapping
                    else f"missing mapping for {exchange_name} in instruments"
                ),
                elapsed_ms=0,
            )
        )

        if not has_mapping:
            reports.append(report)
            continue

        for op_name in (
            "get_balance [DRY-RUN]",
            "get_position [DRY-RUN]",
            "get_open_orders [DRY-RUN]",
            "get_reference_price [DRY-RUN]",
        ):
            report.add(
                StepResult(
                    exchange=exchange_name.upper(),
                    operation=op_name,
                    params=f"instrument={instrument}",
                    endpoint=endpoint,
                    ok=True,
                    detail="NOT EXECUTED — offline dry-run mode",
                    elapsed_ms=0,
                )
            )

        report.add(
            StepResult(
                exchange=exchange_name.upper(),
                operation="place_limit_order [DRY-RUN]",
                params=(
                    f"instrument={instrument} side=buy "
                    f"price=<ref*(1-{price_offset_pct}/100)> amount={test_amount} "
                    "post_only=True reduce_only=False"
                ),
                endpoint=endpoint,
                ok=True,
                detail="NOT EXECUTED — offline dry-run mode",
                elapsed_ms=0,
            )
        )
        report.add(
            StepResult(
                exchange=exchange_name.upper(),
                operation="cancel_order [DRY-RUN]",
                params="order_id=<would-be-placed>",
                endpoint=endpoint,
                ok=True,
                detail="NOT EXECUTED — offline dry-run mode",
                elapsed_ms=0,
            )
        )
        reports.append(report)
    return reports


# ───────────────────────────────────────────────────────────────────────────
# Adapter factory
# ───────────────────────────────────────────────────────────────────────────


def _build_adapters(
    config: ControllerConfig,
    exchange: str,
) -> list[ExchangeAdapter]:
    """Создать адаптеры на основе конфига и флага --exchange."""
    ext_map: dict[str, str] = {}
    nado_map: dict[str, int] = {}
    variational_map: dict[str, str] = {}
    for inst in config.instruments:
        if inst.extended_market_name:
            ext_map[inst.symbol] = inst.extended_market_name
        if inst.nado_product_id is not None:
            nado_map[inst.symbol] = inst.nado_product_id
        if inst.variational_underlying:
            variational_map[inst.symbol] = inst.variational_underlying

    adapters: list[ExchangeAdapter] = []
    requested = ["extended", "nado"] if exchange == "both" else [exchange]

    if "extended" in requested:
        if not ext_map:
            raise ValueError("Для проверки Extended в instruments нужен extended_market_name")
        adapters.append(
            ExtendedAdapter(
                env_file=config.extended_env_file,
                instrument_map=ext_map,
            )
        )

    if "nado" in requested:
        if not nado_map:
            raise ValueError("Для проверки Nado в instruments нужен nado_product_id")
        adapters.append(
            NadoAdapter(
                env_file=config.nado_env_file,
                instrument_map=nado_map,
                network=config.nado_network,
                subaccount_name=config.nado_subaccount_name,
            )
        )

    if "variational" in requested:
        if not variational_map:
            raise ValueError("Для проверки Variational в instruments нужен variational_underlying")
        adapters.append(
            VariationalAdapter(
                env_file=config.variational_env_file or "Variational/.env",
                instrument_map=variational_map,
            )
        )

    return adapters


# ───────────────────────────────────────────────────────────────────────────
# Summary
# ───────────────────────────────────────────────────────────────────────────


def _print_summary(reports: list[VerifyReport]) -> bool:
    """Напечатать итоговую таблицу. Возвращает True если всё PASS."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("ИТОГ ВЕРИФИКАЦИИ")
    logger.info("=" * 70)

    all_pass = True
    for rpt in reports:
        v = rpt.verdict
        if v != "PASS":
            all_pass = False
        logger.info("  %-10s : %s", rpt.exchange.upper(), v)
        for step in rpt.steps:
            mark = "✓" if step.ok else "✗"
            logger.info("    %s %-28s %s", mark, step.operation, step.detail)

    logger.info("-" * 70)
    overall = "PASS" if all_pass else "FAIL"
    logger.info("  OVERALL: %s", overall)
    logger.info("=" * 70)

    return all_pass


# ───────────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Верификация выставления/отмены ордеров через адаптеры контроллера.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Безопасный режим без инициализации адаптеров и без сетевых вызовов. "
            "Если не указать --live, этот режим включается по умолчанию."
        ),
    )
    p.add_argument(
        "--live",
        action="store_true",
        help=(
            "Реальное выставление тестового ордера. Требует CONFIRM_LIVE_TRADING=1, "
            "иначе запуск будет заблокирован."
        ),
    )

    p.add_argument(
        "--exchange",
        choices=["extended", "nado", "variational", "both"],
        default="extended",
        help="Какую биржу проверять (по умолчанию extended).",
    )
    p.add_argument(
        "--no-cancel",
        action="store_true",
        help="Не отменять тестовый ордер после выставления (только --live).",
    )
    p.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Путь к config.yaml контроллера (по умолчанию config.yaml в DN/).",
    )
    p.add_argument(
        "--price-offset-pct",
        type=float,
        default=10.0,
        help="Сдвиг цены от mark в %% (по умолчанию 10%% — далеко от рынка).",
    )
    p.add_argument(
        "--test-amount",
        type=float,
        default=0.002,
        help="Объём тестового ордера в базовом активе (по умолч. 0.002 BTC).",
    )
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING"],
        default="INFO",
    )
    args = p.parse_args()
    if args.live and args.dry_run:
        p.error("Флаги --live и --dry-run взаимоисключающие")
    if not args.live:
        args.dry_run = True
    return args


async def _main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)

    # ── Загрузка конфига ────────────────────────────────────────────────
    try:
        config = load_config(args.config)
    except Exception as e:
        logger.error("Ошибка загрузки конфига: %s", e)
        sys.exit(1)

    if not config.instruments:
        logger.error("В конфиге не задан ни один инструмент (instruments: [])")
        sys.exit(1)

    instrument = config.instruments[0].symbol
    if args.live:
        try:
            require_live_confirmation(
                live_flag=True,
                action_name="verify_order_placement",
            )
        except LiveTradingSafetyError as e:
            logger.error("!!! %s", e)
            sys.exit(2)

    is_live = args.live

    logger.info("=" * 70)
    logger.info("ВЕРИФИКАЦИЯ ОРДЕРОВ")
    logger.info(
        "  Режим       : %s",
        "LIVE" if is_live else "DRY-RUN (offline, no adapter init/network)",
    )
    logger.info("  Биржи       : %s", args.exchange)
    logger.info("  Инструмент  : %s", instrument)
    logger.info("  Тест. объём : %s", args.test_amount)
    logger.info("  Сдвиг цены  : %s%%", args.price_offset_pct)
    logger.info("  Отмена      : %s", "нет" if args.no_cancel else "да")
    logger.info("=" * 70)

    if is_live:
        network_info = []
        if args.exchange in ("extended", "both"):
            network_info.append(f"Extended: {config.extended_network}")
        if args.exchange in ("nado", "both"):
            network_info.append(f"Nado: {config.nado_network}")
        if args.exchange == "variational":
            network_info.append("Variational: mainnet")
        logger.warning(
            "⚠  LIVE режим — будут выставлены РЕАЛЬНЫЕ ордера! Сети: %s",
            ", ".join(network_info),
        )
    else:
        logger.warning(
            "DRY-RUN: адаптеры не инициализируются, сетевые запросы и ордера полностью отключены."
        )
        reports = _run_offline_dry_run(
            config=config,
            exchange=args.exchange,
            instrument=instrument,
            test_amount=args.test_amount,
            price_offset_pct=args.price_offset_pct,
        )
        all_pass = _print_summary(reports)
        sys.exit(0 if all_pass else 1)

    # ── Создание адаптеров ──────────────────────────────────────────────
    try:
        adapters = _build_adapters(config, args.exchange)
    except Exception as e:
        logger.error("Ошибка создания адаптеров: %s", e)
        sys.exit(1)

    if not adapters:
        logger.error("Не удалось создать ни одного адаптера")
        sys.exit(1)

    reports: list[VerifyReport] = []

    for adapter in adapters:
        report = VerifyReport(exchange=adapter.name)
        name = adapter.name.upper()

        logger.info("")
        logger.info("━" * 50)
        logger.info("  Проверка: %s", name)
        logger.info("━" * 50)

        # ── Инициализация ───────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            await adapter.initialize()
            dt = (time.monotonic() - t0) * 1000
            ep = _get_endpoint(adapter)
            report.add(
                StepResult(
                    exchange=name,
                    operation="initialize",
                    params="",
                    endpoint=ep,
                    ok=True,
                    detail=f"endpoint={ep}",
                    elapsed_ms=dt,
                )
            )
        except Exception as e:
            dt = (time.monotonic() - t0) * 1000
            report.add(
                StepResult(
                    exchange=name,
                    operation="initialize",
                    params="",
                    endpoint="<failed>",
                    ok=False,
                    detail=str(e),
                    elapsed_ms=dt,
                )
            )
            reports.append(report)
            continue

        # ── Сценарий ────────────────────────────────────────────────────
        test_amount = args.test_amount
        try:
            if is_live:
                await _run_live(
                    adapter,
                    instrument,
                    report,
                    test_amount=test_amount,
                    price_offset_pct=args.price_offset_pct,
                    no_cancel=args.no_cancel,
                )
            else:
                await _run_dry_run(
                    adapter,
                    instrument,
                    report,
                    test_amount=args.test_amount,
                    price_offset_pct=args.price_offset_pct,
                )
        except Exception as e:
            report.add(
                StepResult(
                    exchange=name,
                    operation="scenario",
                    params="",
                    endpoint=_get_endpoint(adapter),
                    ok=False,
                    detail=f"unhandled: {e}",
                    elapsed_ms=0,
                )
            )
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

        reports.append(report)

    # ── Итог ────────────────────────────────────────────────────────────
    all_pass = _print_summary(reports)
    sys.exit(0 if all_pass else 1)


def main() -> None:
    """Точка входа (синхронная обёртка)."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
