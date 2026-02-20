# Extended Exchange Trading Bot

Базовый торговый бот для биржи Extended Exchange с функциями получения данных пользователя, поиска рынков и ордербука, выставления и закрытия ордеров.

## Структура проекта

```
arb/extended-module/
├── python_sdk/          # Python SDK от Extended Exchange
├── bot/                 # Модуль торгового бота
│   ├── __init__.py
│   ├── config.py        # Конфигурация (API ключи, настройки)
│   ├── client.py        # Инициализация торгового клиента
│   ├── account.py       # Модуль работы с аккаунтом
│   ├── markets.py       # Модуль работы с рынками и ордербуком
│   ├── orders.py        # Модуль работы с ордерами
│   └── trading_bot.py   # Основной класс бота
└── examples/
    └── basic_bot_example.py  # Пример использования
```

## Установка

1. Убедитесь, что установлен Python 3.10 или выше
2. Установите зависимости из `python_sdk/`:
   ```bash
   cd python_sdk
   poetry install
   # или
   pip install -e .
   ```

3. Установите дополнительные зависимости (если нужно):
   ```bash
   pip install python-dotenv
   ```

## Настройка

Создайте файл `.env` в корне проекта `arb/extended-module/` со следующими переменными:

```env
X10_API_KEY=your_api_key
X10_PUBLIC_KEY=0xyour_public_key
X10_PRIVATE_KEY=0xyour_private_key
X10_VAULT_ID=your_vault_id
X10_ENVIRONMENT=testnet  # или mainnet
```

Эти данные можно получить в [API Management](https://testnet.extended.exchange/api-management) после регистрации на Extended Exchange.

## Использование

### Базовый пример

```python
import asyncio
import sys
from pathlib import Path
from decimal import Decimal

# Добавляем пути для импортов
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "python_sdk"))

from bot.config import ExtendedBotConfig
from bot.trading_bot import ExtendedTradingBot
from x10.perpetual.orders import OrderSide

async def main():
    # Загрузка конфигурации
    config = ExtendedBotConfig.from_env()
    
    # Создание бота
    bot = ExtendedTradingBot(config)
    
    try:
        # Получение баланса
        balance = await bot.account.get_balance()
        print(f"Баланс: {balance.data.balance}")
        
        # Поиск рынка
        market = await bot.markets.find_market("BTC-USD")
        
        # Получение ордербука
        orderbook = await bot.markets.subscribe_orderbook("BTC-USD", start=True)
        await asyncio.sleep(2)  # Ждем обновлений
        
        best_bid, best_ask = bot.markets.get_best_bid_ask("BTC-USD")
        print(f"Best bid: {best_bid.price}, Best ask: {best_ask.price}")
        
        # Выставление ордера
        order = await bot.orders.place_order(
            market_name="BTC-USD",
            amount=Decimal("0.01"),
            price=Decimal("50000"),
            side=OrderSide.BUY,
            post_only=True
        )
        print(f"Ордер размещен: {order.data.id}")
        
        # Закрытие ордера
        await bot.orders.cancel_order(order.data.id)
        
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### Запуск примера

```bash
cd /root/Cursor\ Developing/arb/extended-module
python examples/basic_bot_example.py
```

**Примечание:** Скрипт автоматически добавляет необходимые пути в `sys.path`, поэтому можно запускать напрямую из корня проекта.

## Основные функции

### AccountManager (bot.account)

- `get_user_info()` - получение информации об аккаунте
- `get_balance()` - получение баланса
- `get_positions()` - получение открытых позиций
- `get_open_orders()` - получение открытых ордеров

### MarketsManager (bot.markets)

- `find_market(market_name)` - поиск рынка по имени
- `get_market_info(market_name)` - получение информации о рынке
- `get_orderbook_snapshot(market_name)` - получение снимка ордербука через REST API
- `subscribe_orderbook(market_name)` - подписка на ордербук через WebSocket
- `get_best_bid_ask(market_name)` - получение лучших цен bid/ask

### OrdersManager (bot.orders)

- `place_order(...)` - выставление ордера
- `cancel_order(order_id)` - закрытие ордера по ID
- `cancel_order_by_external_id(external_id)` - закрытие ордера по внешнему ID
- `cancel_all_orders(...)` - закрытие всех ордеров или по фильтрам
- `get_order_status(order_id)` - получение статуса ордера

## Документация API

Полная документация API доступна по адресу: https://api.docs.extended.exchange/

## Лицензия

См. файл LICENSE в `python_sdk/`
