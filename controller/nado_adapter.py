"""Адаптер Nado Protocol — реализация ExchangeAdapter.

Обёртка вокруг ExchangeClient из Nado-бота, нормализующая все данные
в единый формат для контроллера.

Nado SDK — синхронный, поэтому вызовы оборачиваются в asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Добавляем Nado в sys.path для импорта.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_NADO_ROOT = _REPO_ROOT / "Nado"
_LEGACY_NADO_GRID_ROOT = _REPO_ROOT / "legacy" / "Nado_grid_bot"

_NADO_ROOT_STR = str(_NADO_ROOT.resolve())  # Абсолютный путь
if _NADO_ROOT_STR not in sys.path:
    sys.path.insert(0, _NADO_ROOT_STR)

_NADO_SDK = _NADO_ROOT / "nado-python-sdk"
_NADO_SDK_STR = str(_NADO_SDK.resolve())  # Абсолютный путь
if _NADO_SDK_STR not in sys.path:
    sys.path.insert(0, _NADO_SDK_STR)

# Runtime SDK patching intentionally removed.


from controller.interface import ExchangeAdapter
from controller.models import (
    NormalizedBalance,
    NormalizedOrder,
    NormalizedPosition,
    PlacedOrderResult,
    PositionDirection,
    Side,
)

logger = logging.getLogger("dn.nado")

# x18 constant
X18 = 10**18


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _x18_to_float(val: int | str) -> float:
    """Конвертация x18 → float."""
    return int(val) / X18


def _float_to_x18(val: float) -> int:
    """Конвертация float → x18."""
    return int(val * X18)


def _order_side_from_amount(amount: int | str) -> Side:
    """Определить сторону по знаку amount (Nado: +buy, -sell)."""
    return Side.BUY if int(amount) > 0 else Side.SELL


def _position_direction_from_size(size_x18: int) -> PositionDirection:
    if size_x18 > 0:
        return PositionDirection.LONG
    elif size_x18 < 0:
        return PositionDirection.SHORT
    return PositionDirection.FLAT


# ---------------------------------------------------------------------------
# NadoAdapter
# ---------------------------------------------------------------------------


class NadoAdapter(ExchangeAdapter):
    """Адаптер Nado Protocol."""

    def __init__(
        self,
        env_file: str | None = None,
        config_path: str | None = None,
        instrument_map: dict[str, int] | None = None,
        network: str = "mainnet",
        private_key: str = "",
        subaccount_name: str = "default",
    ):
        """
        Args:
            env_file: Путь к .env файлу Nado.
            config_path: Путь к config.yaml Nado (для полной инициализации).
            instrument_map: Маппинг логических символов → product_id.
                            Напр. {"BTC-PERP": 2}.
            network: mainnet / testnet / devnet.
            private_key: Приватный ключ (если не из .env).
            subaccount_name: Имя субаккаунта.
        """
        self._env_file = env_file or str(_NADO_ROOT / ".env")
        self._config_path = config_path
        self._instrument_map = instrument_map or {}
        self._network = network
        self._private_key = private_key
        self._subaccount_name = subaccount_name
        self._client = None  # будет ExchangeClient после initialize()

    # -- Properties ----------------------------------------------------------

    @property
    def name(self) -> str:
        return "nado"

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Адаптер не инициализирован. Вызовите initialize() первым.")
        return self._client

    # -- Маппинг инструментов ------------------------------------------------

    def _to_product_id(self, instrument: str) -> int:
        pid = self._instrument_map.get(instrument)
        if pid is None:
            raise ValueError(
                f"Инструмент '{instrument}' не найден в instrument_map. "
                f"Доступные: {list(self._instrument_map.keys())}"
            )
        return pid

    # -- Жизненный цикл ------------------------------------------------------

    async def initialize(self) -> None:
        import os
        from dotenv import load_dotenv

        # Убеждаемся, что Nado в sys.path (на случай, если модуль перезагружался)
        if _NADO_ROOT_STR not in sys.path:
            sys.path.insert(0, _NADO_ROOT_STR)
        if _NADO_SDK_STR not in sys.path:
            sys.path.insert(0, _NADO_SDK_STR)

        # Загружаем .env для секретов
        load_dotenv(self._env_file)
        pk = self._private_key or os.environ.get("NADO_PRIVATE_KEY", "")
        sub = self._subaccount_name or os.environ.get("NADO_SUBACCOUNT_NAME", "default")

        if not pk:
            raise ValueError("NADO_PRIVATE_KEY не задан ни в конфиге, ни в .env")

        # Определяем, где находится legacy bot client (текущий или перенесённый в legacy/).
        bot_roots = [
            _NADO_ROOT / "bot",
            _LEGACY_NADO_GRID_ROOT / "bot",
        ]
        bot_root = next((p for p in bot_roots if (p / "__init__.py").exists()), None)
        if bot_root is None:
            raise RuntimeError(
                "Nado integration is unavailable: bot module was not found in "
                f"{_NADO_ROOT / 'bot'} or {_LEGACY_NADO_GRID_ROOT / 'bot'}."
            )

        bot_parent = bot_root.parent
        bot_parent_str = str(bot_parent.resolve())
        if bot_parent_str not in sys.path:
            sys.path.insert(0, bot_parent_str)

        # Создаём минимальный BotConfig-совместимый объект для ExchangeClient
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _MinimalConfig:
            network: str = self._network
            private_key: str = pk
            subaccount_name: str = sub
            order_ttl_sec: int = 86400
            backoff_base_sec: int = 1
            spot_leverage: bool = False

        # Импортируем ExchangeClient из Nado/bot
        # Удаляем bot из sys.modules если он загружен не из нужного места
        if "bot" in sys.modules:
            bot_module = sys.modules["bot"]
            if hasattr(bot_module, "__file__"):
                bot_file = Path(bot_module.__file__).resolve()
                expected_bot_file = (_NADO_ROOT / "bot" / "__init__.py").resolve()
                if bot_file != expected_bot_file:
                    # Удаляем неправильный модуль
                    del sys.modules["bot"]
                    # Удаляем и подмодули
                    for key in list(sys.modules.keys()):
                        if key.startswith("bot."):
                            del sys.modules[key]

        try:
            from bot.exchange_client import ExchangeClient
        except Exception as e:
            raise RuntimeError(
                "Nado integration is unavailable: failed to import bot client from "
                f"{bot_root}."
            ) from e

        self._client = ExchangeClient(_MinimalConfig())
        logger.info("Nado adapter initialized (network=%s)", self._network)

    async def close(self) -> None:
        logger.info("Nado adapter closed")

    # -- Async обёртка для sync SDK ------------------------------------------

    async def _run_sync(self, fn, *args, **kwargs):
        """Запуск синхронной функции в thread pool."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _wait_order_gone(
        self,
        instrument: str,
        order_id: str,
        *,
        attempts: int = 8,
        sleep_sec: float = 0.5,
    ) -> bool:
        """Подтвердить, что ордер исчез из open orders.

        На Nado ответ cancel может приходить с parse-ошибкой в SDK, поэтому
        итог проверяем фактом отсутствия ордера в открытых.
        """
        for _ in range(attempts):
            try:
                open_orders = await self.get_open_orders(instrument)
            except Exception:
                await asyncio.sleep(sleep_sec)
                continue
            if not any(o.id == order_id for o in open_orders):
                return True
            await asyncio.sleep(sleep_sec)
        return False

    # -- Баланс --------------------------------------------------------------

    async def get_balance(self) -> NormalizedBalance:
        summary = await self._run_sync(self.client.get_subaccount_summary)

        # Nado: health[0] = initial, health содержит assets/liabilities/health
        # assets — общий капитал, health — доступно
        healths = summary.healths
        if healths:
            equity = _x18_to_float(healths[0].assets)
            # health = доступная маржа (assets - liabilities * weight)
            available = _x18_to_float(healths[0].health)
        else:
            equity = 0.0
            available = 0.0

        return NormalizedBalance(
            equity=equity,
            available=available,
            currency="USDC",
        )

    # -- Позиция -------------------------------------------------------------

    async def get_position(self, instrument: str) -> NormalizedPosition:
        product_id = self._to_product_id(instrument)
        summary = await self._run_sync(self.client.get_subaccount_summary)

        # Ищем perp balance по product_id
        size_x18 = 0
        for pb in summary.perp_balances:
            if pb.product_id == product_id:
                size_x18 = int(pb.balance.amount)
                break

        size = _x18_to_float(size_x18)
        direction = _position_direction_from_size(size_x18)

        # Получаем mark price
        mark_x18 = await self._run_sync(self.client.get_mark_price, product_id)
        mark = _x18_to_float(mark_x18)

        return NormalizedPosition(
            instrument=instrument,
            size=size,
            direction=direction,
            entry_price=0.0,  # Nado SDK не отдаёт entry_price напрямую
            mark_price=mark,
            unrealised_pnl=0.0,  # можно рассчитать, но нет entry_price
        )

    # -- Открытые ордера -----------------------------------------------------

    def _orders_from_raw_list(self, orders_raw: list, instrument: str) -> list[NormalizedOrder]:
        """Собрать NormalizedOrder из списка (объекты с .digest/.amount или dict)."""
        result = []
        for o in orders_raw:
            if hasattr(o, "digest"):
                digest = o.digest
                amount_str = o.amount
                unfilled_str = o.unfilled_amount
                price_str = o.price_x18
            else:
                digest = o.get("digest", "")
                amount_str = o.get("amount", "0")
                unfilled_str = o.get("unfilled_amount", "0")
                price_str = o.get("price_x18", "0")
            amount_raw = int(amount_str)
            unfilled_raw = int(unfilled_str)
            total_abs = abs(amount_raw)
            filled = total_abs - abs(unfilled_raw)
            result.append(
                NormalizedOrder(
                    id=f"nado:{digest}",
                    instrument=instrument,
                    side=_order_side_from_amount(amount_str),
                    price=_x18_to_float(int(price_str)),
                    amount=_x18_to_float(total_abs),
                    filled=_x18_to_float(max(0, filled)),
                    post_only=True,
                    reduce_only=False,
                )
            )
        return result

    async def get_open_orders(self, instrument: str) -> list[NormalizedOrder]:
        product_id = self._to_product_id(instrument)
        try:
            orders = await self._run_sync(self.client.get_open_orders, product_id)
            return self._orders_from_raw_list(orders, instrument)
        except Exception as e:
            # Fallback: SDK иногда отдаёт сырой JSON в исключении при ошибке парсинга
            err_str = str(e).strip()
            if err_str.startswith("{") and "data" in err_str:
                try:
                    data = json.loads(err_str)
                    if data.get("status") == "success" and "data" in data:
                        orders_list = data["data"].get("orders", [])
                        return self._orders_from_raw_list(orders_list, instrument)
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
            raise

    # -- Референсная цена ----------------------------------------------------

    async def get_reference_price(self, instrument: str) -> float:
        product_id = self._to_product_id(instrument)
        mark_x18 = await self._run_sync(self.client.get_mark_price, product_id)
        return _x18_to_float(mark_x18)

    async def get_best_bid_ask(self, instrument: str) -> tuple[float, float]:
        product_id = self._to_product_id(instrument)
        market_price = await self._run_sync(self.client.client.market.get_latest_market_price, product_id)
        bid = _x18_to_float(int(getattr(market_price, "bid_x18", "0") or "0"))
        ask = _x18_to_float(int(getattr(market_price, "ask_x18", "0") or "0"))
        return bid, ask

    async def _prepare_order_values(
        self,
        product_id: int,
        side: Side,
        price: float,
        amount: float,
    ) -> tuple[int, int]:
        """Подготовить и округлить цену/объём по правилам Nado."""
        if price <= 0:
            raise ValueError("price должен быть > 0")
        if amount <= 0:
            raise ValueError("amount должен быть > 0")

        signed_amount = _float_to_x18(amount) if side == Side.BUY else -_float_to_x18(amount)
        price_x18 = _float_to_x18(price)

        book_info = await self._run_sync(self.client.get_book_info, product_id)
        from nado_protocol.utils.math import round_x18

        price_x18 = round_x18(price_x18, book_info.price_increment_x18)

        abs_amount = abs(signed_amount)
        remainder = abs_amount % book_info.size_increment
        if remainder != 0:
            abs_amount = abs_amount + (book_info.size_increment - remainder)

        signed_amount = abs_amount if side == Side.BUY else -abs_amount
        return price_x18, signed_amount

    # -- Выставление ордера --------------------------------------------------

    async def place_limit_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        post_only: bool = True,
        reduce_only: bool = False,
        external_id: Optional[str] = None,
    ) -> PlacedOrderResult:
        product_id = self._to_product_id(instrument)

        try:
            price_x18, signed_amount = await self._prepare_order_values(
                product_id=product_id,
                side=side,
                price=price,
                amount=amount,
            )

            if post_only:
                digest, _ = await self._run_sync(
                    self.client.place_post_only_order,
                    product_id,
                    price_x18,
                    signed_amount,
                    reduce_only,
                )
            else:
                # In current ExchangeClient wrapper non-post-only limit is not exposed.
                # Fallback to IOC/taker path to honor post_only=False semantics.
                logger.warning("Nado place_limit_order called with post_only=False, using IOC path")
                digest, _ = await self._run_sync(
                    self.client.place_ioc_order,
                    product_id,
                    price_x18,
                    signed_amount,
                    reduce_only,
                    external_id,
                )
            order_id = f"nado:{digest}"
            logger.info(
                "Order placed on Nado: %s %s %s @ %s, id=%s",
                side.value,
                amount,
                instrument,
                price,
                order_id,
            )
            return PlacedOrderResult(id=order_id, success=True)
        except Exception as e:
            logger.error("Failed to place order on Nado: %s", e)
            return PlacedOrderResult(id="", success=False, error=str(e))

    # -- IOC (taker) ордер ---------------------------------------------------

    async def place_ioc_order(
        self,
        instrument: str,
        side: Side,
        price: float,
        amount: float,
        reduce_only: bool = False,
        external_id: Optional[str] = None,
    ) -> PlacedOrderResult:
        """Aggressive исполнение через IOC.

        Используем как «маркет»: ставим цену с отклонением и флаг IOC, чтобы
        либо исполниться сразу, либо отмениться.
        """
        product_id = self._to_product_id(instrument)

        try:
            price_x18, signed_amount = await self._prepare_order_values(
                product_id=product_id,
                side=side,
                price=price,
                amount=amount,
            )

            digest, _ = await self._run_sync(
                self.client.place_ioc_order,
                product_id,
                price_x18,
                signed_amount,
                reduce_only,
                external_id,
            )
            order_id = f"nado:{digest}"
            logger.info(
                "IOC order placed on Nado: %s %s %s @ %s, id=%s",
                side.value,
                amount,
                instrument,
                price,
                order_id,
            )
            return PlacedOrderResult(id=order_id, success=True)
        except Exception as e:
            logger.error("Failed to place IOC order on Nado: %s", e)
            return PlacedOrderResult(id="", success=False, error=str(e))

    # -- Отмена ордера -------------------------------------------------------

    async def cancel_order(self, instrument: str, order_id: str) -> bool:
        product_id = self._to_product_id(instrument)
        digest = order_id.replace("nado:", "")
        try:
            await self._run_sync(self.client.cancel_order, product_id, digest)
            logger.info("Order cancelled on Nado: %s", order_id)
            return await self._wait_order_gone(instrument, order_id, attempts=10, sleep_sec=0.4)
        except Exception as e:
            error_msg = str(e)
            # Workaround: SDK падает на парсинге ответа (cancel_orders: missing field `tx`).
            # Проверяем, отменился ли ордер на сервере — даём время на обработку и смотрим open orders.
            if "missing field `tx`" in error_msg and "cancel_orders" in error_msg:
                if await self._wait_order_gone(instrument, order_id, attempts=10, sleep_sec=0.5):
                    logger.warning(
                        "Cancel order %s on Nado: parsing error in SDK but order no longer in open orders → treated as success",
                        order_id,
                    )
                    return True
            logger.error("Failed to cancel order %s on Nado: %s", order_id, e)
            return False

    async def cancel_all_orders(self, instrument: str) -> int:
        product_id = self._to_product_id(instrument)
        try:
            orders = await self.get_open_orders(instrument)
            count = len(orders)
            try:
                await self._run_sync(self.client.cancel_all_orders, product_id)
                # проверяем, что действительно снято
                after = await self.get_open_orders(instrument)
                done = max(count - len(after), 0)
                logger.info("Cancelled %d/%d orders on Nado for %s", done, count, instrument)
                return done
            except Exception as e:
                msg = str(e)
                # Fallback: если cancel_all_orders не сработал — отменяем поштучно
                ok = 0
                for o in orders:
                    if await self.cancel_order(instrument, o.id):
                        ok += 1
                logger.warning(
                    "Cancel_all_orders fallback: cancelled %d/%d individually (cause: %s)",
                    ok,
                    count,
                    msg,
                )
                # Финальная проверка (даём шлюзу время консистентно обновить open orders).
                await asyncio.sleep(0.8)
                after = await self.get_open_orders(instrument)
                still_open = len(after)
                if still_open > 0:
                    logger.error(
                        "Cancel_all_orders fallback: %d orders still open on Nado", still_open
                    )
                return ok
        except Exception as e:
            logger.error("Failed to cancel all orders on Nado: %s", e)
            return 0
