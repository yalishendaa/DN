#!/usr/bin/env python3
"""Открытие или закрытие дельта-нейтральной позиции.

Логика:
1) Смотрим текущие позиции на primary/secondary биржах из config.yaml.
   - Если обе нулевые → открываем (параметры из config.yaml, секция entry).
   - Если есть позиция → спрашиваем, закрыть ли её. Отказ — выходим.
2) Лимитка на primary.
3) Ждём исполнения лимитки → ставим taker/IOC на secondary в противоположную сторону.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import logging
import re
import signal
import sys
import time
import warnings
from pathlib import Path

import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
_DN_ROOT = _SCRIPT_DIR.parent.parent
if str(_DN_ROOT) not in sys.path:
    sys.path.insert(0, str(_DN_ROOT))

from controller.config import ControllerConfig, load_config
from controller.interface import ExchangeAdapter
from controller.models import Side

LOG_FMT = "%(asctime)s | %(levelname)-5s | %(message)s"
DATE_FMT = "%H:%M:%S"
logger = logging.getLogger("enter_dn")


# --------------------------------------------------------------------------- #
# CLI                                                                        #
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    """Минимум флагов: все параметры из config.yaml (секция entry), кроме цены лимитки."""
    p = argparse.ArgumentParser(description="Enter/close delta-neutral position (config-driven)")
    p.add_argument("--config", "-c", default="config.yaml", help="Путь к config.yaml")
    p.add_argument(
        "--live",
        action="store_true",
        help=(
            "Разрешить реальные ордера. Без флага запуск блокируется."
        ),
    )
    return p.parse_args()


class ConsoleLogFilter(logging.Filter):
    """Controls verbosity for full/compact console output."""

    def __init__(self, primary_exchange: str, compact: bool):
        super().__init__()
        self.primary_exchange = primary_exchange
        self.compact = compact

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())

        # In full mode only suppress the noisiest repetitive market snapshots for Nado-primary runs.
        if not self.compact:
            if self.primary_exchange == "nado" and "mark_price_fetched" in msg:
                return False
            return True

        # Compact mode: hide known noisy technical warnings.
        noisy_warning_tokens = (
            "Nado execute: raw /execute response status=200 body_keys=['place_order']",
        )
        if any(token in msg for token in noisy_warning_tokens):
            return False

        # Keep non-noisy warnings/errors.
        if record.levelno >= logging.WARNING:
            return True

        # Compact mode: keep actionable script logs + startup info; drop noisy SDK telemetry.
        if "mark_price_fetched" in msg or "book_info_fetched" in msg:
            return False

        if record.name == "enter_dn":
            return True

        startup_keep = (
            "Extended adapter initialized",
            "Nado adapter initialized",
            "Initializing default mainnet context",
            "sdk_init_success",
        )
        if any(token in msg for token in startup_keep):
            return True

        return False


# --------------------------------------------------------------------------- #
# Helpers                                                                    #
# --------------------------------------------------------------------------- #


async def wait_fill(
    adapter: ExchangeAdapter,
    instrument: str,
    target_side: Side,
    start_pos: float,
    target_delta: float,
    poll: float,
    initial_price: float,
    get_ref_price,
    reprice_interval_sec: float,
    reprice_threshold_pct: float,
    reprice_offset_pct: float,
    cancel_all_orders,
    place_limit_order,
    has_open_order=None,
    shutdown_event: asyncio.Event | None = None,
) -> float:
    """Ждёт fill и при сильном уходе рынка переставляет лимитку дальше от ref."""
    epsilon = 1e-9
    placed_price = initial_price
    last_reprice_ts = time.monotonic()
    prev_dist_pct: float | None = None
    missing_order_since: float | None = None

    def _filled_delta(current: float) -> float:
        if target_side == Side.BUY:
            raw = current - start_pos
        else:
            raw = start_pos - current
        return max(0.0, min(target_delta, raw))

    while True:
        pos = await adapter.get_position(instrument)
        current = pos.size

        filled = _filled_delta(current)
        if filled >= target_delta * 0.999:
            return filled
        if shutdown_event is not None and shutdown_event.is_set():
            await cancel_all_orders()
            return filled

        # Если ордер пропал из open orders и fill нет, не висим бесконечно:
        # перевыставляем лимитку по текущему ref.
        if has_open_order is not None and filled < target_delta - epsilon:
            try:
                order_is_open = await has_open_order()
            except Exception as e:
                logger.warning("Не удалось проверить open orders на primary: %s", e)
                order_is_open = True
            now = time.monotonic()
            if not order_is_open:
                if missing_order_since is None:
                    missing_order_since = now
                if now - missing_order_since >= max(1.0, poll * 3):
                    ref = await get_ref_price()
                    if ref > 0:
                        remaining = max(target_delta - filled, 0.0)
                        if remaining > epsilon:
                            delta = ref * reprice_offset_pct
                            new_price = ref - delta if target_side == Side.BUY else ref + delta
                            re_res = await place_limit_order(new_price, remaining)
                            if re_res.success:
                                logger.warning(
                                    "Primary ордер пропал из open orders, перевыставили: "
                                    "%s %.6f %s @ %.4f (ref=%.4f)",
                                    target_side.value,
                                    remaining,
                                    instrument,
                                    new_price,
                                    ref,
                                )
                                placed_price = new_price
                                prev_dist_pct = None
                                missing_order_since = None
                            else:
                                logger.warning(
                                    "Primary ордер отсутствует, но перевыставить не удалось: %s",
                                    re_res.error,
                                )
                                missing_order_since = now
            else:
                missing_order_since = None

        now = time.monotonic()
        if reprice_interval_sec > 0 and (now - last_reprice_ts) >= reprice_interval_sec:
            last_reprice_ts = now
            ref = await get_ref_price()
            if ref > 0:
                dist_pct = abs(placed_price - ref) / ref
                # Переставляем только когда цена УДАЛЯЕТСЯ от лимитки.
                # Если цена приближается (dist уменьшается) — не трогаем ордер.
                if prev_dist_pct is None:
                    prev_dist_pct = dist_pct
                moving_away = dist_pct > (prev_dist_pct + epsilon)
                prev_dist_pct = dist_pct

                if moving_away and dist_pct > max(reprice_threshold_pct, epsilon):
                    remaining = max(target_delta - filled, 0.0)
                    if remaining > epsilon:
                        await cancel_all_orders()
                        delta = ref * reprice_offset_pct
                        new_price = ref - delta if target_side == Side.BUY else ref + delta
                        re_res = await place_limit_order(new_price, remaining)
                        if re_res.success:
                            logger.info(
                                "Лимитка переставлена: %s %.6f %s @ %.4f (prev=%.4f, ref=%.4f, dist=%.5f)",
                                target_side.value,
                                remaining,
                                instrument,
                                new_price,
                                placed_price,
                                ref,
                                dist_pct,
                            )
                            placed_price = new_price
                            prev_dist_pct = None
                        else:
                            logger.warning("Не удалось переставить лимитку: %s", re_res.error)

        await asyncio.sleep(poll)


# --------------------------------------------------------------------------- #
# Main flow                                                                  #
# --------------------------------------------------------------------------- #


async def main() -> None:
    args = parse_args()

    logging.basicConfig(format=LOG_FMT, datefmt=DATE_FMT, level=logging.INFO)
    shutdown_event = asyncio.Event()
    if not args.live:
        logger.error("!!! LIVE TRADING BLOCKED for `enter_delta_neutral`: rerun with --live")
        raise SystemExit(2)

    def _request_shutdown() -> None:
        if not shutdown_event.is_set():
            logger.warning(
                "Получен сигнал остановки. Дожидаемся завершения текущего шага и корректно завершаем..."
            )
            shutdown_event.set()
        else:
            logger.warning("Повторный сигнал остановки. Завершение уже в процессе.")

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Fallback для окружений, где add_signal_handler недоступен.
            signal.signal(sig, lambda _s, _f: _request_shutdown())

    cfg: ControllerConfig = load_config(args.config)

    # ── Читаем entry-параметры из config.yaml (+ опциональный config.advanced.yaml) ──
    cfg_path = Path(args.config)
    raw_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_cfg, dict):
        raise SystemExit("Корневой YAML в config.yaml должен быть mapping (dict)")

    advanced_path = cfg_path.with_name("config.advanced.yaml")
    advanced_entry: dict[str, object] = {}
    if advanced_path.exists():
        advanced_cfg = yaml.safe_load(advanced_path.read_text(encoding="utf-8")) or {}
        if not isinstance(advanced_cfg, dict):
            raise SystemExit("Корневой YAML в config.advanced.yaml должен быть mapping (dict)")
        raw_advanced_entry = advanced_cfg.get("entry", {})
        if raw_advanced_entry and not isinstance(raw_advanced_entry, dict):
            raise SystemExit("Секция entry в config.advanced.yaml должна быть mapping (dict)")
        if isinstance(raw_advanced_entry, dict):
            advanced_entry = raw_advanced_entry
        logger.info("Загружены advanced-параметры из %s", advanced_path)

    raw_entry = raw_cfg.get("entry", {})
    if raw_entry and not isinstance(raw_entry, dict):
        raise SystemExit("Секция entry в config.yaml должна быть mapping (dict)")
    # Приоритет у основного config.yaml, advanced — только источник дефолтов.
    entry = {**advanced_entry, **(raw_entry if isinstance(raw_entry, dict) else {})}

    symbol = entry.get("instrument") or (cfg.instruments[0].symbol if cfg.instruments else None)
    if not symbol:
        raise SystemExit("Не найден instrument в config.yaml (entry.instrument или instruments[0])")

    open_size = entry.get("size")  # размер одного шага при открытии
    if open_size is not None:
        open_size = float(open_size)
    target_size = entry.get("target_size")
    target_size = float(target_size) if target_size is not None else None

    open_direction = entry.get("direction") or "long"  # long / short относительно primary_exchange
    if open_direction not in ("long", "short"):
        raise SystemExit("entry.direction должен быть long или short")
    allowed_exchanges = {"extended", "nado", "variational"}
    default_secondary = {
        "extended": "variational",
        "nado": "extended",
        "variational": "extended",
    }
    primary_exchange = str(entry.get("primary_exchange", cfg.entry_primary_exchange)).lower()
    if primary_exchange not in allowed_exchanges:
        raise SystemExit("entry.primary_exchange должен быть extended | nado | variational")

    secondary_raw = entry.get("secondary_exchange")
    if secondary_raw is None or (isinstance(secondary_raw, str) and not secondary_raw.strip()):
        secondary_exchange = default_secondary[primary_exchange]
    else:
        secondary_exchange = str(secondary_raw).lower()
    if secondary_exchange not in allowed_exchanges:
        raise SystemExit("entry.secondary_exchange должен быть extended | nado | variational")
    if secondary_exchange == primary_exchange:
        raise SystemExit("entry.secondary_exchange должен отличаться от entry.primary_exchange")

    compact = entry.get("log_mode", "full") == "compact"
    console_filter = ConsoleLogFilter(primary_exchange, compact)
    # Attach to handlers (not only to root logger) so propagated child-logger records are filtered too.
    for handler in logging.getLogger().handlers:
        handler.addFilter(console_filter)
    if compact:
        # Hide repetitive pydantic runtime warnings in compact mode.
        warnings.filterwarnings(
            "ignore",
            message=r"Valid config keys have changed in V2:.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r"Pydantic serializer warnings:.*",
            category=UserWarning,
        )

    offset_pct = float(entry.get("offset_pct", 0.02))  # доля (0.02 = 2%) для открытия
    offset_retry_pct = float(entry.get("offset_retry_pct", offset_pct * 2))
    close_offset_pct = float(entry.get("close_offset_pct", 0.02))  # доля для закрытия
    close_offset_retry_pct = float(entry.get("close_offset_retry_pct", close_offset_pct * 2))
    slippage_pct = float(entry.get("slippage_pct", 0.05))
    secondary_slippage_pct = float(entry.get("secondary_slippage_pct", slippage_pct))
    ioc_min_cross_pct = float(entry.get("ioc_min_cross_pct", 0.2))  # минимум сдвига для IOC, %
    post_only = bool(entry.get("post_only", True))
    hedge_margin_buffer = float(entry.get("hedge_margin_buffer", 0.90))
    close_min_notional = float(entry.get("close_min_notional", 105.0))
    # Если post-only ордер пересекает книгу, увеличиваем отступ и пробуем снова.
    # 1.30 = сделать оффсет на 30% дальше.
    post_only_fallback_factor = float(entry.get("post_only_fallback_factor", 1.50))
    post_only_fallback_retries = int(entry.get("post_only_fallback_retries", 4))
    post_only_fallback_max_pct = float(entry.get("post_only_fallback_max_pct", 0.30))
    poll_interval = float(entry.get("poll_interval", 1.5))
    reprice_interval_sec = float(entry.get("reprice_interval_sec", 30.0))
    hedge_confirm_timeout_sec = float(entry.get("hedge_confirm_timeout_sec", 3.0))
    hedge_confirm_poll_sec = float(entry.get("hedge_confirm_poll_sec", 0.2))
    hedge_retry_count = int(entry.get("hedge_retry_count", 2))
    hedge_retry_slippage_mult = float(entry.get("hedge_retry_slippage_mult", 1.8))
    hedge_retry_max_slippage_pct = float(entry.get("hedge_retry_max_slippage_pct", 1.0))
    if post_only_fallback_factor <= 1.0:
        post_only_fallback_factor = 1.50
    if post_only_fallback_retries < 0:
        post_only_fallback_retries = 0
    if post_only_fallback_max_pct <= 0:
        post_only_fallback_max_pct = 0.30
    if hedge_margin_buffer <= 0 or hedge_margin_buffer > 1:
        hedge_margin_buffer = 0.90
    if close_min_notional <= 0:
        close_min_notional = 105.0
    if hedge_confirm_timeout_sec < 0:
        hedge_confirm_timeout_sec = 0.0
    if hedge_confirm_poll_sec <= 0:
        hedge_confirm_poll_sec = 0.2
    if hedge_retry_count < 0:
        hedge_retry_count = 0
    if hedge_retry_slippage_mult <= 1.0:
        hedge_retry_slippage_mult = 1.8
    if hedge_retry_max_slippage_pct <= 0:
        hedge_retry_max_slippage_pct = 1.0

    inst_cfg = next((i for i in cfg.instruments if i.symbol == symbol), None)
    if not inst_cfg:
        known = ", ".join(i.symbol for i in cfg.instruments) or "нет инструментов в config.yaml"
        raise SystemExit(f"Инструмент {symbol} не найден в config.yaml (доступно: {known})")

    selected_exchanges = {primary_exchange, secondary_exchange}
    adapters: dict[str, ExchangeAdapter] = {}
    extended_adapter: ExchangeAdapter | None = None

    if "extended" in selected_exchanges:
        from controller.extended_adapter import ExtendedAdapter

        if not inst_cfg.extended_market_name:
            raise SystemExit(
                f"Для пары {primary_exchange}+{secondary_exchange} у {symbol} должен быть "
                "заполнен instruments[].extended_market_name"
            )
        extended_adapter = ExtendedAdapter(
            env_file=cfg.extended_env_file,
            instrument_map={symbol: inst_cfg.extended_market_name},
        )
        adapters["extended"] = extended_adapter

    if "nado" in selected_exchanges:
        from controller.nado_adapter import NadoAdapter

        if inst_cfg.nado_product_id is None:
            raise SystemExit(
                f"Для пары {primary_exchange}+{secondary_exchange} у {symbol} должен быть "
                "заполнен instruments[].nado_product_id"
            )
        nado = NadoAdapter(
            env_file=cfg.nado_env_file,
            instrument_map={symbol: inst_cfg.nado_product_id},
            network=cfg.nado_network,
            subaccount_name=cfg.nado_subaccount_name,
        )
        adapters["nado"] = nado

    if "variational" in selected_exchanges:
        from controller.variational_adapter import VariationalAdapter

        if not inst_cfg.variational_underlying:
            raise SystemExit(
                f"Для пары {primary_exchange}+{secondary_exchange} у {symbol} должен быть "
                "заполнен instruments[].variational_underlying"
            )
        variational = VariationalAdapter(
            env_file=cfg.variational_env_file or "Variational/.env",
            instrument_map={symbol: inst_cfg.variational_underlying},
        )
        adapters["variational"] = variational

    primary_adapter = adapters.get(primary_exchange)
    secondary_adapter = adapters.get(secondary_exchange)
    if primary_adapter is None or secondary_adapter is None:
        raise SystemExit(
            "Не удалось создать primary/secondary адаптеры. "
            f"primary={primary_exchange}, secondary={secondary_exchange}"
        )

    logger.info(
        "Инициализация адаптеров: primary=%s secondary=%s",
        primary_adapter.name,
        secondary_adapter.name,
    )
    for adapter in adapters.values():
        await adapter.initialize()

    log_path = Path("logs/trades.csv")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    realized_total = 0.0
    fees_total = 0.0

    def log_cycle_result(
        symbol: str,
        start_total_equity: float,
        end_total_equity: float,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        header = ["datetime_utc", "token", "balance_open", "balance_close"]
        row = [
            now,
            symbol,
            f"{start_total_equity:.6f}",
            f"{end_total_equity:.6f}",
        ]
        write_header = not log_path.exists()
        with log_path.open("a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(header)
            writer.writerow(row)

    async def summary_loop():
        while True:
            if shutdown_event.is_set():
                return
            try:
                primary_pos = await primary_adapter.get_position(symbol)
                secondary_pos = await secondary_adapter.get_position(symbol)
                primary_ref = await primary_adapter.get_reference_price(symbol)
                secondary_ref = await secondary_adapter.get_reference_price(symbol)
                primary_bal = await primary_adapter.get_balance()
                secondary_bal = await secondary_adapter.get_balance()
                unreal_pnl = primary_pos.unrealised_pnl + secondary_pos.unrealised_pnl
                logger.info(
                    "SUMMARY | primary(%s)_pos=%.6f entry=%.2f mark=%.2f "
                    "| secondary(%s)_pos=%.6f mark=%.2f "
                    "| primary_ref=%.2f secondary_ref=%.2f "
                    "| primary_bal=%.2f secondary_bal=%.2f "
                    "| unreal_pnl=%.2f | fees=%.2f | realized=%.2f",
                    primary_adapter.name,
                    primary_pos.size,
                    primary_pos.entry_price,
                    primary_pos.mark_price,
                    secondary_adapter.name,
                    secondary_pos.size,
                    secondary_pos.mark_price,
                    primary_ref,
                    secondary_ref,
                    primary_bal.equity,
                    secondary_bal.equity,
                    unreal_pnl,
                    fees_total,
                    realized_total,
                )
            except Exception as e:
                logger.warning("SUMMARY error: %s", e)
            await asyncio.sleep(30)

    summary_task = asyncio.create_task(summary_loop())

    start_primary_bal = await primary_adapter.get_balance()
    start_secondary_bal = await secondary_adapter.get_balance()
    start_total_equity = start_primary_bal.equity + start_secondary_bal.equity
    logger.info(
        "Стартовый суммарный equity: %.4f (primary[%s]=%.4f, secondary[%s]=%.4f)",
        start_total_equity,
        primary_adapter.name,
        start_primary_bal.equity,
        secondary_adapter.name,
        start_secondary_bal.equity,
    )

    # Снимаем стартовые позиции
    primary_pos0 = (await primary_adapter.get_position(symbol)).size
    secondary_pos0 = (await secondary_adapter.get_position(symbol)).size
    logger.info(
        "Стартовые позиции: Primary(%s)=%.6f, Secondary(%s)=%.6f",
        primary_adapter.name,
        primary_pos0,
        secondary_adapter.name,
        secondary_pos0,
    )

    # Решаем действие: close если есть позиции и пользователь согласился; иначе open
    epsilon = 1e-9
    has_position = abs(primary_pos0) > epsilon or abs(secondary_pos0) > epsilon
    if has_position:
        ans = (
            input(
                "Обнаружены позиции "
                f"(primary[{primary_adapter.name}]={primary_pos0:.6f}, "
                f"secondary[{secondary_adapter.name}]={secondary_pos0:.6f}). "
                "Закрыть их? [y/N]: "
            )
            .strip()
            .lower()
        )
        if ans in ("y", "yes"):
            action = "close"
        else:
            action = "open"
            logger.info("Продолжаем набор позиции до target_size (режим open).")
    else:
        action = "open"

    if action == "open":
        if open_size is None:
            raise SystemExit("Для открытия позиции укажите entry.size в config.yaml")
        size = open_size
        remaining_target = target_size
    else:  # close
        if open_size is None:
            raise SystemExit(
                "Для закрытия позиции укажите entry.size в config.yaml (chunk размера)"
            )
        size = open_size
        remaining_target = None  # закрываем до нуля

    total_filled = 0.0
    if action == "open" and remaining_target is not None:
        desired_sign = 1.0 if open_direction == "long" else -1.0
        already_on_target_side = max(0.0, desired_sign * primary_pos0)
        total_filled = min(remaining_target, already_on_target_side)
        logger.info(
            "Текущий объём на primary в целевом направлении: %.6f / %.6f",
            total_filled,
            remaining_target,
        )

    def _is_post_only_cross_error(error: str | None) -> bool:
        if not error:
            return False
        lowered = error.lower()
        return (
            ("post-only" in lowered and "cross" in lowered)
            or "post_only_failed" in lowered
            or ("post-only" in lowered and "failed" in lowered)
        )

    def _filled_by_side(start_pos: float, end_pos: float, side: Side, cap: float) -> float:
        raw = (end_pos - start_pos) if side == Side.BUY else (start_pos - end_pos)
        return max(0.0, min(cap, raw))

    def _parse_extended_1140(error: str | None) -> tuple[float, float] | None:
        if not error:
            return None
        if "code\":1140" not in error and "code 1140" not in error:
            return None
        m = re.search(r"Order cost ([0-9]+(?:\.[0-9]+)?) exceeds available for trade ([0-9]+(?:\.[0-9]+)?)", error)
        if not m:
            return None
        return float(m.group(1)), float(m.group(2))

    async def _clear_primary_open_orders(max_attempts: int = 4) -> bool:
        """Гарантированно очистить активные open orders на primary перед новой постановкой.

        Важно для Nado: без верификации после cancel возможен stacking ордеров.
        """
        for attempt in range(1, max_attempts + 1):
            await primary_adapter.cancel_all_orders(symbol)
            await asyncio.sleep(min(0.35 * attempt, 1.5))
            open_orders = await primary_adapter.get_open_orders(symbol)
            active = [
                o
                for o in open_orders
                if abs((o.amount or 0.0) - (o.filled or 0.0)) > 1e-9
            ]
            if not active:
                return True

            # Поштучная отмена как fallback.
            for o in active:
                await primary_adapter.cancel_order(symbol, o.id)

            await asyncio.sleep(0.4)
            open_orders = await primary_adapter.get_open_orders(symbol)
            active = [
                o
                for o in open_orders
                if abs((o.amount or 0.0) - (o.filled or 0.0)) > 1e-9
            ]
            if not active:
                return True

            logger.warning(
                "Primary open orders still present after cancel attempt %d/%d: %d",
                attempt,
                max_attempts,
                len(active),
            )
        return False

    async def _max_open_amount_on_extended(ref_price: float) -> float:
        """Оценка максимального объёма, который Extended может открыть сейчас."""
        if extended_adapter is None or not inst_cfg.extended_market_name:
            return 0.0
        bal = await extended_adapter.get_balance()
        market = await extended_adapter.bot.markets.find_market(inst_cfg.extended_market_name)
        max_leverage = float(getattr(market.trading_config, "max_leverage", 1) or 1)
        eff_price = max(ref_price, 1e-9)
        return max(0.0, (bal.available * max_leverage * hedge_margin_buffer) / eff_price)

    try:
        while True:
            if shutdown_event.is_set():
                logger.info("Остановка запрошена: новые шаги не запускаем.")
                break
            # Обновляем позиции и ref перед каждой попыткой
            primary_pos_curr = (await primary_adapter.get_position(symbol)).size
            secondary_pos_curr = (await secondary_adapter.get_position(symbol)).size
            if (
                action == "close"
                and abs(primary_pos_curr) <= epsilon
                and abs(secondary_pos_curr) <= epsilon
            ):
                break

            ref_primary = await primary_adapter.get_reference_price(symbol)
            if ref_primary <= 0:
                raise SystemExit(
                    f"Не удалось получить ref_price primary ({primary_adapter.name})"
                )

            if action == "open":
                # long = лонг на primary, short на secondary
                primary_long = open_direction == "long"
                # ВАЖНО: direction задаётся относительно primary exchange,
                # поэтому сторона primary одинакова независимо от выбора биржи.
                side_primary = Side.BUY if primary_long else Side.SELL
            else:
                # Закрытие: ориентируемся на позицию primary биржи
                if abs(primary_pos_curr) <= epsilon and abs(secondary_pos_curr) <= epsilon:
                    break
                if primary_pos_curr > epsilon:
                    side_primary = Side.SELL  # уменьшаем long
                elif primary_pos_curr < -epsilon:
                    side_primary = Side.BUY  # уменьшаем short
                else:
                    side_primary = Side.BUY if secondary_pos_curr > 0 else Side.SELL

            best_bid, best_ask = await primary_adapter.get_best_bid_ask(symbol)
            if side_primary == Side.BUY:
                anchor_primary = best_bid if best_bid > 0 else ref_primary
                anchor_label = "bid" if best_bid > 0 else "ref"
            else:
                anchor_primary = best_ask if best_ask > 0 else ref_primary
                anchor_label = "ask" if best_ask > 0 else "ref"

            if action == "close":
                offsets = [close_offset_pct, close_offset_retry_pct]
            else:
                offsets = [offset_pct, offset_retry_pct]

            chosen_limit = None
            last_error = None
            primary_post_only_used = post_only
            offsets_queue = list(offsets)
            offset_idx = 0
            extra_fallbacks = 0
            while offset_idx < len(offsets_queue):
                off = offsets_queue[offset_idx]
                offset_idx += 1
                delta_price = anchor_primary * off
                price_try = (
                    anchor_primary - delta_price
                    if side_primary == Side.BUY
                    else anchor_primary + delta_price
                )
                chunk = None  # will set later

                # Размер шага
                if action == "open":
                    remaining = (
                        remaining_target - total_filled if remaining_target is not None else size
                    )
                    if remaining <= epsilon:
                        chosen_limit = price_try
                        chunk = 0
                        break
                    chunk = min(size, remaining)
                    if primary_adapter.name == "extended" and extended_adapter is not None:
                        bal = await extended_adapter.get_balance()
                        market = await extended_adapter.bot.markets.find_market(
                            inst_cfg.extended_market_name
                        )
                        max_leverage = float(getattr(market.trading_config, "max_leverage", 1) or 1)
                        needed_margin = (chunk * price_try) / max_leverage
                        if bal.available + 1e-9 < needed_margin:
                            last_error = (
                                "Недостаточно средств на Extended: нужно маржи "
                                f"{needed_margin:.4f}, доступно {bal.available:.4f}"
                            )
                            continue
                    elif (
                        primary_adapter.name == "nado"
                        and secondary_adapter.name == "extended"
                        and extended_adapter is not None
                    ):
                        # Pre-check вторичной ноги: не открываем на primary объём, который нечем захеджировать на Extended.
                        ref_secondary = await secondary_adapter.get_reference_price(symbol)
                        est_hedge_price = ref_secondary * (
                            1 + (max(secondary_slippage_pct, ioc_min_cross_pct) / 100)
                        )
                        max_secondary = await _max_open_amount_on_extended(est_hedge_price)
                        if max_secondary <= epsilon:
                            last_error = (
                                "Недостаточно средств для хеджа на Extended: доступный объём ~0. "
                                "Ставка на primary пропущена."
                            )
                            continue
                        if chunk > max_secondary + 1e-9:
                            logger.warning(
                                "Урезаем chunk из-за лимита маржи secondary(Extended): %.6f -> %.6f",
                                chunk,
                                max_secondary,
                            )
                            chunk = max_secondary
                else:
                    basis = abs(primary_pos_curr)
                    if basis <= epsilon:
                        # fallback: если primary уже плоский, но вторичный еще не успел схлопнуться
                        basis = abs(secondary_pos_curr)
                    chunk = min(size, basis)
                    # Для close учитываем минимум notional на обеих ногах, чтобы не зависнуть на "пыли".
                    ref_secondary = await secondary_adapter.get_reference_price(symbol)
                    min_amount_primary = close_min_notional / max(ref_primary, 1e-9)
                    min_amount_secondary = close_min_notional / max(ref_secondary, 1e-9)
                    min_close_amount = max(min_amount_primary, min_amount_secondary)

                    if basis + 1e-9 < min_close_amount:
                        raise SystemExit(
                            "Остаток позиции ниже минимального размера закрытия на одной из бирж: "
                            f"basis={basis:.6f}, need>={min_close_amount:.6f} "
                            f"(notional>={close_min_notional:.2f})."
                        )

                    if chunk + 1e-9 < min_close_amount:
                        logger.info(
                            "Увеличиваем close chunk до минимального notional: %.6f -> %.6f",
                            chunk,
                            min_close_amount,
                        )
                        chunk = min(basis, min_close_amount)

                    remainder_after_chunk = max(0.0, basis - chunk)
                    if remainder_after_chunk > epsilon and remainder_after_chunk + 1e-9 < min_close_amount:
                        logger.info(
                            "Остаток после частичного close был бы ниже min_notional "
                            "(amount=%.6f < %.6f), закрываем весь остаток %.6f",
                            remainder_after_chunk,
                            min_close_amount,
                            basis,
                        )
                        chunk = basis
                    if chunk <= epsilon:
                        chosen_limit = price_try
                        break

                # Пытаемся выставить ордер с данным off
                logger.info(
                    "%s лимитка на %s: %s %.6f %s @ %.4f "
                    "(ref=%.4f, %s=%.4f, off=%.5f, remaining=%.6f)",
                    "Закрываем" if action == "close" else "Открываем",
                    primary_adapter.name,
                    side_primary.value,
                    chunk,
                    symbol,
                    price_try,
                    ref_primary,
                    anchor_label,
                    anchor_primary,
                    off,
                    remaining if action == "open" else chunk,
                )
                if not await _clear_primary_open_orders():
                    last_error = (
                        f"Не удалось очистить open orders на primary ({primary_adapter.name}); "
                        "новая лимитка не будет выставлена, чтобы не накапливать ордера."
                    )
                    logger.error("%s", last_error)
                    await asyncio.sleep(max(1.0, poll_interval))
                    continue
                # Для primary всегда уважаем post_only из конфига.
                # Если post-only пересекает книгу, сработает fallback с увеличением off.
                post_flag = post_only
                primary_post_only_used = post_flag
                place_res = await primary_adapter.place_limit_order(
                    instrument=symbol,
                    side=side_primary,
                    price=price_try,
                    amount=chunk,
                    post_only=post_flag,
                    reduce_only=(action == "close"),
                )

                if place_res.success:
                    chosen_limit = price_try
                    break
                else:
                    last_error = place_res.error
                    logger.warning(
                        "Лимитка не выставлена на %s: side=%s qty=%.6f %s @ %.4f "
                        "(ref=%.4f, off=%.5f, err=%s)",
                        primary_adapter.name,
                        side_primary.value,
                        chunk,
                        symbol,
                        price_try,
                        ref_primary,
                        off,
                        place_res.error,
                    )
                    if (
                        primary_post_only_used
                        and _is_post_only_cross_error(place_res.error)
                        and extra_fallbacks < post_only_fallback_retries
                    ):
                        next_off = min(off * post_only_fallback_factor, post_only_fallback_max_pct)
                        if next_off > off + 1e-9:
                            offsets_queue.append(next_off)
                            extra_fallbacks += 1
                            logger.warning(
                                "Post-only пересек книгу на %s: off=%.5f -> retry off=%.5f (%d/%d)",
                                primary_adapter.name,
                                off,
                                next_off,
                                extra_fallbacks,
                                post_only_fallback_retries,
                            )
                    elif (
                        primary_adapter.name == "extended"
                        and extra_fallbacks < post_only_fallback_retries
                    ):
                        next_off = min(off * post_only_fallback_factor, post_only_fallback_max_pct)
                        if next_off > off + 1e-9:
                            offsets_queue.append(next_off)
                            extra_fallbacks += 1
                            logger.warning(
                                "Extended placement retry: off=%.5f -> %.5f (%d/%d), reason=%s",
                                off,
                                next_off,
                                extra_fallbacks,
                                post_only_fallback_retries,
                                place_res.error,
                            )
                    continue

            if chosen_limit is None:
                logger.error(
                    "Не удалось выставить лимитный ордер на primary (%s): %s. Повторим цикл.",
                    primary_exchange,
                    last_error,
                )
                await asyncio.sleep(max(1.0, poll_interval))
                continue

            reprice_threshold_pct = close_offset_pct if action == "close" else offset_pct
            reprice_offset_pct = close_offset_retry_pct if action == "close" else offset_retry_pct

            async def _get_ref_primary() -> float:
                bid_p, ask_p = await primary_adapter.get_best_bid_ask(symbol)
                if side_primary == Side.BUY:
                    return bid_p if bid_p > 0 else await primary_adapter.get_reference_price(symbol)
                return ask_p if ask_p > 0 else await primary_adapter.get_reference_price(symbol)

            async def _cancel_primary_orders() -> None:
                if not await _clear_primary_open_orders():
                    raise RuntimeError(
                        f"Не удалось очистить open orders на primary ({primary_adapter.name})"
                    )

            async def _place_primary_limit(
                price: float,
                amount: float,
                *,
                _side: Side = side_primary,
                _post_only: bool = primary_post_only_used,
                _reduce_only: bool = (action == "close"),
            ):
                return await primary_adapter.place_limit_order(
                    instrument=symbol,
                    side=_side,
                    price=price,
                    amount=amount,
                    post_only=_post_only,
                    reduce_only=_reduce_only,
                )

            async def _has_primary_open_order() -> bool:
                orders = await primary_adapter.get_open_orders(symbol)
                for o in orders:
                    if o.side != side_primary:
                        continue
                    if abs((o.amount or 0.0) - (o.filled or 0.0)) <= 1e-9:
                        continue
                    return True
                return False

            # Быстрый polling fill для обеих бирж: после fill нужно максимально быстро отправить хедж.
            poll_eff = min(poll_interval, 0.2)

            try:
                filled = await wait_fill(
                    adapter=primary_adapter,
                    instrument=symbol,
                    target_side=side_primary,
                    start_pos=primary_pos_curr,
                    target_delta=chunk,
                    poll=poll_eff,
                    initial_price=chosen_limit,
                    get_ref_price=_get_ref_primary,
                    reprice_interval_sec=reprice_interval_sec,
                    reprice_threshold_pct=reprice_threshold_pct,
                    reprice_offset_pct=reprice_offset_pct,
                    cancel_all_orders=_cancel_primary_orders,
                    place_limit_order=_place_primary_limit,
                    has_open_order=_has_primary_open_order,
                    shutdown_event=shutdown_event,
                )
            except RuntimeError as e:
                logger.error(
                    "Ошибка контроля primary-ордеров (%s): %s. Повторим цикл без новой постановки.",
                    primary_adapter.name,
                    e,
                )
                await asyncio.sleep(max(1.0, poll_interval))
                continue
            if shutdown_event.is_set() and filled <= epsilon:
                logger.info("Остановка после снятия primary ордера (fill=0).")
                break

            # Готовим вторичный ордер (taker/IOC)
            side_second = Side.SELL if side_primary == Side.BUY else Side.BUY
            secondary = secondary_adapter

            async def _place_ioc(
                ref_price: float,
                slip_pct: float,
                amount: float,
                *,
                _secondary: ExchangeAdapter = secondary,
                _side_second: Side = side_second,
                _reduce_only: bool = (action == "close"),
            ):
                slip_val = ref_price * (slip_pct / 100)
                price = ref_price + slip_val if _side_second == Side.BUY else ref_price - slip_val
                logger.info(
                    "IOC/taker на %s: %s %.6f %s @ %.4f (ref=%.4f, slippage=%.4f%%)",
                    _secondary.name,
                    _side_second.value,
                    amount,
                    symbol,
                    price,
                    ref_price,
                    slip_pct,
                )
                if _secondary.name == "nado":
                    return await _secondary.place_ioc_order(
                        instrument=symbol,
                        side=_side_second,
                        price=price,
                        amount=amount,
                        reduce_only=_reduce_only,
                    )
                # Extended/Variational taker: aggressive non-post-only лимитка.
                return await _secondary.place_limit_order(
                    instrument=symbol,
                    side=_side_second,
                    price=price,
                    amount=amount,
                    post_only=False,
                    reduce_only=_reduce_only,
                )

            ref_second, secondary_pos_before = await asyncio.gather(
                secondary.get_reference_price(symbol),
                secondary.get_position(symbol),
            )
            ref_second = float(ref_second)
            secondary_pos_before = secondary_pos_before.size
            if ref_second <= 0:
                raise SystemExit("Не удалось получить ref_price вторичной биржи")

            # Fast hedge: сначала моментально отправляем вторую ногу, потом уже делаем доп. валидации/ретраи.
            slip_base = secondary_slippage_pct if secondary.name == "extended" else slippage_pct
            slip_try = max(slip_base, ioc_min_cross_pct)
            ioc_res = await _place_ioc(ref_second, slip_try, filled)

            # Если не пересекает книгу (2056), пробуем ещё раз с удвоенным сдвигом
            if (
                not ioc_res.success
                and ioc_res.error
                and "does not cross the book" in ioc_res.error.lower()
            ):
                slip_try *= 2
                ioc_res = await _place_ioc(ref_second, slip_try, filled)

            # Для secondary=Extended в open-режиме при 1140 пробуем уменьшить размер и повторить.
            if not ioc_res.success and action == "open" and isinstance(secondary, ExtendedAdapter):
                parsed = _parse_extended_1140(ioc_res.error)
                if parsed:
                    order_cost, available_for_trade = parsed
                    scale = max(0.0, min(1.0, (available_for_trade / max(order_cost, 1e-9)) * 0.98))
                    reduced_amount = filled * scale
                    if reduced_amount > 1e-8:
                        logger.warning(
                            "Extended 1140: уменьшаем объём хеджа %.6f -> %.6f (available=%.6f, cost=%.6f)",
                            filled,
                            reduced_amount,
                            available_for_trade,
                            order_cost,
                        )
                        ioc_res = await _place_ioc(ref_second, slip_try, reduced_amount)

            # Позиция secondary может обновляться с задержкой (особенно на Variational),
            # поэтому подтверждаем хедж коротким polling перед аварийным unwind.
            hedge_deadline = time.monotonic() + hedge_confirm_timeout_sec
            hedged_amount = 0.0
            secondary_pos_after = secondary_pos_before
            while True:
                secondary_pos_after = (await secondary.get_position(symbol)).size
                hedged_amount = _filled_by_side(
                    start_pos=secondary_pos_before,
                    end_pos=secondary_pos_after,
                    side=side_second,
                    cap=filled,
                )
                if hedged_amount >= filled - 1e-8:
                    break
                if (not ioc_res.success) or time.monotonic() >= hedge_deadline:
                    break
                await asyncio.sleep(hedge_confirm_poll_sec)

            residual_unhedged = max(0.0, filled - hedged_amount)

            # Если secondary ордер принят, но хедж получился неполным, пробуем добрать остаток
            # несколькими taker-ордерaми с увеличением slippage.
            retry_idx = 0
            while (
                ioc_res.success
                and residual_unhedged > 1e-8
                and retry_idx < hedge_retry_count
            ):
                retry_idx += 1
                ref_second_retry = await secondary.get_reference_price(symbol)
                if ref_second_retry <= 0:
                    break
                slip_try = min(
                    hedge_retry_max_slippage_pct,
                    max(ioc_min_cross_pct, slip_try * hedge_retry_slippage_mult),
                )
                logger.warning(
                    "Secondary residual %.6f after hedge; retry %d/%d with slippage=%.4f%%",
                    residual_unhedged,
                    retry_idx,
                    hedge_retry_count,
                    slip_try,
                )
                retry_res = await _place_ioc(ref_second_retry, slip_try, residual_unhedged)
                if not retry_res.success:
                    ioc_res = retry_res
                    break

                hedge_deadline = time.monotonic() + hedge_confirm_timeout_sec
                while True:
                    secondary_pos_after = (await secondary.get_position(symbol)).size
                    hedged_amount = _filled_by_side(
                        start_pos=secondary_pos_before,
                        end_pos=secondary_pos_after,
                        side=side_second,
                        cap=filled,
                    )
                    residual_unhedged = max(0.0, filled - hedged_amount)
                    if residual_unhedged <= 1e-8 or time.monotonic() >= hedge_deadline:
                        break
                    await asyncio.sleep(hedge_confirm_poll_sec)

            if not ioc_res.success or residual_unhedged > 1e-8:
                logger.error(
                    "Вторичная нога не захеджирована полностью: success=%s hedged=%.6f/%.6f err=%s",
                    ioc_res.success,
                    hedged_amount,
                    filled,
                    ioc_res.error,
                )

                unwind_side = Side.SELL if side_primary == Side.BUY else Side.BUY
                unwind_amount = residual_unhedged if residual_unhedged > 1e-8 else filled

                ref_primary_now = await primary_adapter.get_reference_price(symbol)
                if ref_primary_now <= 0:
                    raise SystemExit(
                        "CRITICAL: вторичная нога не исполнена, а получить ref primary для unwind не удалось"
                    )

                unwind_slip = max(slippage_pct, ioc_min_cross_pct)
                unwind_slip_val = ref_primary_now * (unwind_slip / 100)
                unwind_price = (
                    ref_primary_now + unwind_slip_val
                    if unwind_side == Side.BUY
                    else ref_primary_now - unwind_slip_val
                )

                logger.warning(
                    "Аварийный unwind primary(%s): %s %.6f %s @ %.4f (ref=%.4f, slippage=%.4f%%)",
                    primary_exchange,
                    unwind_side.value,
                    unwind_amount,
                    symbol,
                    unwind_price,
                    ref_primary_now,
                    unwind_slip,
                )
                if primary_adapter.name == "nado":
                    unwind_res = await primary_adapter.place_ioc_order(
                        instrument=symbol,
                        side=unwind_side,
                        price=unwind_price,
                        amount=unwind_amount,
                        reduce_only=(action == "open"),
                    )
                else:
                    unwind_res = await primary_adapter.place_limit_order(
                        instrument=symbol,
                        side=unwind_side,
                        price=unwind_price,
                        amount=unwind_amount,
                        post_only=False,
                        reduce_only=(action == "open"),
                    )

                if not unwind_res.success:
                    raise SystemExit(
                        f"CRITICAL: hedge failed on secondary ({ioc_res.error}); unwind on primary failed ({unwind_res.error})"
                    )
                raise SystemExit(
                    f"Secondary hedge incomplete ({hedged_amount:.6f}/{filled:.6f}); "
                    "primary leg was unwound to avoid one-leg exposure"
                )

            total_filled += filled
            if shutdown_event.is_set():
                logger.info("Остановка запрошена: текущий шаг завершён, выходим.")
                break

            if action == "open" and remaining_target is None:
                break
            if action == "open" and remaining_target is not None:
                if total_filled >= remaining_target - epsilon:
                    break

        logger.info("Всего исполнено на primary(%s): filled=%.6f", primary_adapter.name, total_filled)

        # Финальные позиции
        primary_pos1 = (await primary_adapter.get_position(symbol)).size
        secondary_pos1 = (await secondary_adapter.get_position(symbol)).size
        logger.info(
            "Итоговые позиции: Primary(%s)=%.6f, Secondary(%s)=%.6f, net=%.6f",
            primary_adapter.name,
            primary_pos1,
            secondary_adapter.name,
            secondary_pos1,
            primary_pos1 + secondary_pos1,
        )

        end_primary_bal = await primary_adapter.get_balance()
        end_secondary_bal = await secondary_adapter.get_balance()
        end_total_equity = end_primary_bal.equity + end_secondary_bal.equity
        cycle_pnl = end_total_equity - start_total_equity
        realized_total = cycle_pnl
        logger.info(
            "Cycle PnL (by equity delta): %.6f | start=%.6f end=%.6f | fees=%.6f",
            cycle_pnl,
            start_total_equity,
            end_total_equity,
            fees_total,
        )
        log_cycle_result(
            symbol=symbol,
            start_total_equity=start_total_equity,
            end_total_equity=end_total_equity,
        )
    finally:
        summary_task.cancel()
        # Безопасное завершение: пытаемся снять ордера и закрыть оба адаптера.
        closed: set[int] = set()
        for adapter in (primary_adapter, secondary_adapter):
            if id(adapter) in closed:
                continue
            try:
                await adapter.cancel_all_orders(symbol)
            except Exception as e:
                logger.warning("Cleanup: не удалось снять ордера %s: %s", adapter.name, e)
            try:
                await adapter.close()
            except Exception as e:
                logger.warning("Cleanup: не удалось закрыть адаптер %s: %s", adapter.name, e)
            closed.add(id(adapter))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Остановлено пользователем (KeyboardInterrupt)")
