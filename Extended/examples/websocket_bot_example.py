"""Пример использования торгового бота с WebSocket для получения данных в реальном времени.

Демонстрирует:
- Запуск WebSocket подключения
- Использование кэшированных данных из WebSocket
- Реакцию на обновления в реальном времени через callback'и
- Выставление ордера через REST API с отслеживанием через WebSocket
"""

import asyncio
import logging
import sys
from decimal import Decimal
from pathlib import Path

# Добавляем корневую директорию проекта в путь для импортов
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
# Добавляем путь к python_sdk для импорта пакета x10
sys.path.insert(0, str(project_root / "python_sdk"))

from bot.config import ExtendedBotConfig
from bot.trading_bot import ExtendedTradingBot
from x10.perpetual.balances import BalanceModel
from x10.perpetual.orders import OpenOrderModel, OrderSide, TimeInForce
from x10.perpetual.positions import PositionModel

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Основная функция демонстрации."""
    # Загрузка конфигурации из переменных окружения
    try:
        config = ExtendedBotConfig.from_env()
        logger.info(f"Конфигурация загружена для окружения: {config.environment}")
    except ValueError as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        logger.info("Убедитесь, что установлены переменные окружения:")
        logger.info("  - X10_API_KEY")
        logger.info("  - X10_PUBLIC_KEY")
        logger.info("  - X10_PRIVATE_KEY")
        logger.info("  - X10_VAULT_ID")
        logger.info("  - X10_ENVIRONMENT (опционально, по умолчанию 'testnet')")
        return

    # Создание бота
    bot = ExtendedTradingBot(config)

    # Callback'и для обновлений через WebSocket
    async def on_balance_update(balance: BalanceModel) -> None:
        """Обработчик обновления баланса."""
        logger.info(f"⚡ WebSocket: Баланс обновлен - {balance.balance} {balance.collateral_name}")

    async def on_positions_update(positions: list[PositionModel]) -> None:
        """Обработчик обновления позиций."""
        if positions:
            logger.info(f"⚡ WebSocket: Позиции обновлены - {len(positions)} позиций")
            for pos in positions:
                logger.info(f"  - {pos.market}: {pos.side}, размер: {pos.size}, цена: {pos.mark_price}")
        else:
            logger.info("⚡ WebSocket: Позиции обновлены - позиций нет")

    async def on_orders_update(orders: list[OpenOrderModel]) -> None:
        """Обработчик обновления ордеров."""
        if orders:
            logger.info(f"⚡ WebSocket: Ордера обновлены - {len(orders)} ордеров")
            for order in orders:
                logger.info(
                    f"  - ID: {order.id}, {order.market}: {order.side}, "
                    f"цена: {order.price}, статус: {order.status}"
                )
        else:
            logger.info("⚡ WebSocket: Ордера обновлены - ордеров нет")

    try:
        # Подписка на обновления через WebSocket
        if bot.websocket:
            bot.websocket.subscribe_to_balance_updates(on_balance_update)
            bot.websocket.subscribe_to_positions_updates(on_positions_update)
            bot.websocket.subscribe_to_orders_updates(on_orders_update)

        # Запуск WebSocket подключения
        logger.info("=" * 60)
        logger.info("Запуск WebSocket подключения...")
        logger.info("=" * 60)
        await bot.start_websocket()

        # Ждем немного для получения первых обновлений
        logger.info("Ожидание первых обновлений через WebSocket...")
        await asyncio.sleep(3)

        # 1. Получение данных через кэш WebSocket
        logger.info("\n" + "=" * 60)
        logger.info("1. Получение данных через кэш WebSocket")
        logger.info("=" * 60)

        # Баланс из кэша WebSocket
        balance = await bot.account.get_balance(use_cache=True)
        if balance.data:
            logger.info(f"Баланс (из кэша WebSocket): {balance.data.balance} {balance.data.collateral_name}")
            logger.info(f"Доступно для торговли: {balance.data.available_for_trade}")

        # Позиции из кэша WebSocket
        positions = await bot.account.get_positions(use_cache=True)
        if positions.data:
            logger.info(f"Позиции (из кэша WebSocket): {len(positions.data)} позиций")
            for position in positions.data:
                logger.info(
                    f"  - {position.market}: {position.side}, "
                    f"размер: {position.size}, цена: {position.mark_price}"
                )
        else:
            logger.info("Позиции (из кэша WebSocket): позиций нет")

        # Ордера из кэша WebSocket
        open_orders = await bot.account.get_open_orders(use_cache=True)
        if open_orders.data:
            logger.info(f"Ордера (из кэша WebSocket): {len(open_orders.data)} ордеров")
            for order in open_orders.data:
                logger.info(
                    f"  - ID: {order.id}, {order.market}: {order.side}, "
                    f"цена: {order.price}, статус: {order.status}"
                )
        else:
            logger.info("Ордера (из кэша WebSocket): ордеров нет")

        # 2. Сравнение: кэш WebSocket vs REST API
        logger.info("\n" + "=" * 60)
        logger.info("2. Сравнение: кэш WebSocket vs REST API")
        logger.info("=" * 60)

        # Получаем данные через REST API (принудительно)
        logger.info("Получение данных через REST API (use_cache=False)...")
        balance_rest = await bot.account.get_balance(use_cache=False)
        if balance_rest.data:
            logger.info(f"Баланс (REST API): {balance_rest.data.balance} {balance_rest.data.collateral_name}")

        # 3. Поиск рынка и получение ордербука
        logger.info("\n" + "=" * 60)
        logger.info("3. Поиск рынка и получение ордербука")
        logger.info("=" * 60)

        market_name = "BTC-USD"
        market = await bot.markets.find_market(market_name)
        if not market:
            logger.warning(f"Рынок {market_name} не найден")
            return

        logger.info(f"Рынок найден: {market.name}")
        logger.info(f"  Минимальный размер ордера: {market.trading_config.min_order_size}")

        # Подписка на ордербук через WebSocket
        logger.info("\nПодписка на ордербук через WebSocket...")
        orderbook = await bot.markets.subscribe_orderbook(market_name, start=True)
        await asyncio.sleep(2)

        best_bid, best_ask = bot.markets.get_best_bid_ask(market_name)
        if best_bid and best_ask:
            logger.info("Лучшие цены из ордербука:")
            logger.info(f"  Best bid: {best_bid.price} (количество: {best_bid.amount})")
            logger.info(f"  Best ask: {best_ask.price} (количество: {best_ask.amount})")
            logger.info(f"  Spread: {best_ask.price - best_bid.price}")

        # 4. Выставление ордера через REST API
        logger.info("\n" + "=" * 60)
        logger.info("4. Выставление ордера через REST API")
        logger.info("=" * 60)
        logger.info("Ордер будет отслеживаться через WebSocket обновления")

        if not best_bid or not best_ask:
            logger.warning("Не удалось получить цены из ордербука, пропускаем выставление ордера")
        else:
            # Выставляем ордер ниже лучшего bid
            order_price = best_bid.price - Decimal("100")
            order_amount = market.trading_config.min_order_size

            logger.info(f"Выставляем ордер BUY:")
            logger.info(f"  Рынок: {market_name}")
            logger.info(f"  Цена: {order_price}")
            logger.info(f"  Количество: {order_amount}")

            try:
                placed_order = await bot.orders.place_order(
                    market_name=market_name,
                    amount=order_amount,
                    price=order_price,
                    side=OrderSide.BUY,
                    post_only=True,
                    time_in_force=TimeInForce.GTT,
                )

                if placed_order.data:
                    order_id = placed_order.data.id
                    logger.info(f"Ордер успешно размещен через REST API!")
                    logger.info(f"  ID ордера: {order_id}")

                    # Ждем обновления через WebSocket
                    logger.info("\nОжидание обновления ордера через WebSocket...")
                    await asyncio.sleep(3)

                    # Проверяем ордер через кэш WebSocket
                    orders_after = await bot.account.get_open_orders(use_cache=True)
                    if orders_after.data:
                        found_order = next((o for o in orders_after.data if o.id == order_id), None)
                        if found_order:
                            logger.info(f"Ордер найден в кэше WebSocket:")
                            logger.info(f"  Статус: {found_order.status}")
                            logger.info(f"  Цена: {found_order.price}")
                            logger.info(f"  Количество: {found_order.qty}")

                    # Закрытие ордера через REST API
                    logger.info("\nЗакрытие ордера через REST API...")
                    try:
                        await bot.orders.cancel_order(order_id)
                        logger.info(f"Ордер {order_id} успешно отменен")

                        # Ждем обновления через WebSocket
                        logger.info("Ожидание обновления через WebSocket после отмены...")
                        await asyncio.sleep(2)

                    except Exception as cancel_error:
                        logger.warning(f"Ошибка при отмене ордера: {cancel_error}")

            except Exception as e:
                logger.error(f"Ошибка при работе с ордером: {e}", exc_info=True)

        # 5. Демонстрация работы в реальном времени и статистика
        logger.info("\n" + "=" * 60)
        logger.info("5. Мониторинг обновлений в реальном времени")
        logger.info("=" * 60)
        logger.info("WebSocket продолжает работать и обновлять данные...")
        logger.info("Нажмите Ctrl+C для остановки")

        # Показываем статистику каждые 3 секунды
        import time

        start_time = time.time()
        while time.time() - start_time < 10:
            await asyncio.sleep(3)
            if bot.websocket:
                stats = bot.websocket.get_statistics()
                logger.info("\n📈 Статистика WebSocket:")
                logger.info(f"  Сообщений получено: {stats['messages_received']}")
                logger.info(f"  Время работы: {stats.get('uptime_seconds', 0):.1f} сек")
                logger.info(f"  Баланс в кэше: {'✅' if stats['has_balance'] else '❌'}")
                logger.info(f"  Позиций: {stats['positions_count']}")
                logger.info(f"  Ордеров: {stats['orders_count']}")
                if stats['last_updates']:
                    logger.info("  Последние обновления:")
                    for key, value in stats['last_updates'].items():
                        age = time.time() - value
                        logger.info(f"    {key}: {age:.1f} сек назад")

    except KeyboardInterrupt:
        logger.info("\nПолучен сигнал остановки...")
    except Exception as e:
        logger.error(f"Ошибка при выполнении примера: {e}", exc_info=True)
    finally:
        # Закрытие всех соединений
        logger.info("\n" + "=" * 60)
        logger.info("Закрытие соединений...")
        logger.info("=" * 60)
        await bot.close()
        logger.info("Готово!")


if __name__ == "__main__":
    asyncio.run(main())
