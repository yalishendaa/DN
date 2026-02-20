"""Пример использования базового торгового бота для Extended Exchange.

Демонстрирует основные функции:
1. Получение данных пользователя (баланс, позиции)
2. Поиск рынка (например, "BTC-USD")
3. Получение ордербука (REST и WebSocket)
4. Выставление лимитного ордера
5. Проверка статуса ордера
6. Закрытие ордера
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
from x10.perpetual.orders import OrderSide, TimeInForce

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

    try:
        # 1. Получение данных пользователя
        logger.info("=" * 60)
        logger.info("1. Получение данных пользователя")
        logger.info("=" * 60)

        # Получение информации об аккаунте
        account_info = await bot.account.get_user_info()
        if account_info.data:
            logger.info(f"ID аккаунта: {account_info.data.id}")
            logger.info(f"Email: {account_info.data.email if hasattr(account_info.data, 'email') else 'N/A'}")

        # Получение баланса
        balance = await bot.account.get_balance()
        if balance.data:
            logger.info(f"Баланс: {balance.data.balance} {balance.data.collateral_name}")
            logger.info(f"Доступно для торговли: {balance.data.available_for_trade}")
            logger.info(f"Equity: {balance.data.equity}")

        # Получение позиций
        positions = await bot.account.get_positions()
        if positions.data:
            logger.info(f"Открытых позиций: {len(positions.data)}")
            for position in positions.data:
                logger.info(
                    f"  - {position.market}: {position.side}, "
                    f"размер: {position.size}, цена: {position.mark_price}"
                )
        else:
            logger.info("Открытых позиций нет")

        # Получение открытых ордеров
        open_orders = await bot.account.get_open_orders()
        if open_orders.data:
            logger.info(f"Открытых ордеров: {len(open_orders.data)}")
            for order in open_orders.data:
                logger.info(
                    f"  - ID: {order.id}, {order.market}: {order.side}, "
                    f"цена: {order.price}, количество: {order.qty}"
                )
        else:
            logger.info("Открытых ордеров нет")

        # 2. Поиск рынка
        logger.info("\n" + "=" * 60)
        logger.info("2. Поиск рынка")
        logger.info("=" * 60)

        market_name = "BTC-USD"
        market = await bot.markets.find_market(market_name)
        if market:
            logger.info(f"Рынок найден: {market.name}")
            logger.info(f"  Актив: {market.asset_name}")
            logger.info(f"  Минимальный размер ордера: {market.trading_config.min_order_size}")
            logger.info(f"  Минимальное изменение цены: {market.trading_config.min_price_change}")
            logger.info(f"  Максимальное кредитное плечо: {market.trading_config.max_leverage}")
            logger.info(f"  Последняя цена: {market.market_stats.last_price}")
            logger.info(f"  Mark цена: {market.market_stats.mark_price}")
        else:
            logger.warning(f"Рынок {market_name} не найден")
            logger.info("Доступные рынки можно получить через bot.markets.get_market_info()")
            return

        # 3. Получение ордербука
        logger.info("\n" + "=" * 60)
        logger.info("3. Получение ордербука")
        logger.info("=" * 60)

        # Получение снимка ордербука через REST API
        orderbook_snapshot = await bot.markets.get_orderbook_snapshot(market_name)
        if orderbook_snapshot.data:
            logger.info("Снимок ордербука (REST API):")
            if orderbook_snapshot.data.bid:
                best_bid = orderbook_snapshot.data.bid[0]
                logger.info(f"  Лучший bid: {best_bid.price} (количество: {best_bid.qty})")
            if orderbook_snapshot.data.ask:
                best_ask = orderbook_snapshot.data.ask[0]
                logger.info(f"  Лучший ask: {best_ask.price} (количество: {best_ask.qty})")

        # Подписка на ордербук через WebSocket
        logger.info("\nПодписка на ордербук через WebSocket...")
        orderbook = await bot.markets.subscribe_orderbook(market_name, start=True)

        # Ждем немного для получения обновлений
        await asyncio.sleep(2)

        best_bid, best_ask = bot.markets.get_best_bid_ask(market_name)
        if best_bid and best_ask:
            logger.info("Лучшие цены из WebSocket ордербука:")
            logger.info(f"  Best bid: {best_bid.price} (количество: {best_bid.amount})")
            logger.info(f"  Best ask: {best_ask.price} (количество: {best_ask.amount})")
            logger.info(f"  Spread: {best_ask.price - best_bid.price}")

        # 4. Выставление лимитного ордера
        logger.info("\n" + "=" * 60)
        logger.info("4. Выставление лимитного ордера")
        logger.info("=" * 60)

        if not best_bid or not best_ask:
            logger.warning("Не удалось получить цены из ордербука, пропускаем выставление ордера")
        else:
            # Выставляем ордер ниже лучшего bid (чтобы не исполнился сразу)
            order_price = best_bid.price - Decimal("100")  # На 100 единиц ниже лучшего bid
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
                    post_only=True,  # Только maker ордер
                    time_in_force=TimeInForce.GTT,
                )

                if placed_order.data:
                    order_id = placed_order.data.id
                    logger.info(f"Ордер успешно размещен!")
                    logger.info(f"  ID ордера: {order_id}")
                    logger.info(f"  Внешний ID: {placed_order.data.external_id}")

                    # 5. Проверка статуса ордера
                    logger.info("\n" + "=" * 60)
                    logger.info("5. Проверка статуса ордера")
                    logger.info("=" * 60)

                    await asyncio.sleep(1)  # Небольшая задержка

                    order_status = await bot.orders.get_order_status(order_id)
                    if order_status.data:
                        logger.info(f"Статус ордера {order_id}:")
                        logger.info(f"  Статус: {order_status.data.status}")
                        logger.info(f"  Рынок: {order_status.data.market}")
                        logger.info(f"  Сторона: {order_status.data.side}")
                        logger.info(f"  Цена: {order_status.data.price}")
                        logger.info(f"  Количество: {order_status.data.qty}")
                        logger.info(f"  Исполнено: {order_status.data.filled_qty or 0}")

                    # 6. Закрытие ордера
                    logger.info("\n" + "=" * 60)
                    logger.info("6. Закрытие ордера")
                    logger.info("=" * 60)

                    try:
                        cancel_result = await bot.orders.cancel_order(order_id)
                        # Если исключение не выброшено, значит операция успешна
                        # send_delete_request выбрасывает исключение при ошибках
                        logger.info(f"Ордер {order_id} успешно отменен")
                    except Exception as cancel_error:
                        logger.warning(f"Ошибка при отмене ордера: {cancel_error}")

            except Exception as e:
                logger.error(f"Ошибка при работе с ордером: {e}", exc_info=True)

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
