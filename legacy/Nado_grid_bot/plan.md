---
name: BTC-PERP grid bot
overview: "Пошаговая реализация long-only grid-бота для BTC-PERP (product_id: 2) на Nado mainnet, строго по ТЗ technical_task.md, с использованием локальной копии nado-python-sdk."
todos:
  - id: step-0
    content: "Шаг 0: Скелет проекта, requirements.txt, config.py, logger.py, config.yaml, .env"
    status: pending
  - id: step-1
    content: "Шаг 1: exchange_client -- init SDK, get_mark_price, get_book_info (подключение к mainnet)"
    status: pending
  - id: step-2
    content: "Шаг 2: exchange_client -- place_post_only_order + cancel_order (тест одиночного ордера)"
    status: pending
  - id: step-3
    content: "Шаг 3: grid_engine -- generate_grid_levels, round_up для sell, dry-run вывод"
    status: pending
  - id: step-4
    content: "Шаг 4: state_store -- SQLite: таблицы orders, pending_buffers, fills_log, grid_params"
    status: pending
  - id: step-5
    content: "Шаг 5: execution -- выставление стартовой buy-сетки с throttle и reconcile"
    status: pending
  - id: step-6
    content: "Шаг 6: fills_listener -- WebSocket подписка на fill + fallback polling"
    status: pending
  - id: step-7
    content: "Шаг 7: refill-логика -- on_buy_fill -> sell, on_sell_fill -> buy, буферизация"
    status: pending
  - id: step-8
    content: "Шаг 8: graceful shutdown, restart reconcile, CLI (start/stop/pause/resume/status/dry-run)"
    status: pending
isProject: false
---

# Реализация BTC-PERP Long Grid Bot на Nado

## Карта SDK (ключевые файлы)


| Что нужно | Где в SDK | Ключевые строки |
| --------- | --------- | --------------- |


**Perp API (mark price):**

- `client.perp.get_prices(product_id)` -- [nado_protocol/client/apis/perp/query.py](Nado-grid/nado-python-sdk/nado_protocol/client/apis/perp/query.py) строки 16-30
- Возвращает `IndexerPerpPricesData` с полями `mark_price_x18`, `index_price_x18`, `update_time`

**Типы ордеров (OrderType enum):**

- [nado_protocol/utils/expiration.py](Nado-grid/nado-python-sdk/nado_protocol/utils/expiration.py) строки 5-9: `DEFAULT=0, IOC=1, FOK=2, POST_ONLY=3`

**Post-only / maker-only / reduce-only через appendix:**

- `build_appendix(OrderType.POST_ONLY, reduce_only=False)` -- [nado_protocol/utils/order.py](Nado-grid/nado-python-sdk/nado_protocol/utils/order.py) строки 112-226
- Битовая раскладка: bit 10-9 = order type, bit 11 = reduce_only

**Размещение ордера:**

- `client.market.place_order({"product_id": ..., "order": OrderParams(...)})` -- [nado_protocol/client/apis/market/execute.py](Nado-grid/nado-python-sdk/nado_protocol/client/apis/market/execute.py) строки 66-79
- `OrderParams` поля: `sender`, `priceX18` (int), `amount` (int, +buy/-sell), `expiration`, `appendix`, `nonce`
- `PlaceOrderParams` поля: `product_id`, `order`, `id` (optional), `spot_leverage` (optional)

**Отмена ордеров:**

- `client.market.cancel_orders({"productIds": [...], "digests": [...], "sender": ...})` -- строки 96-109
- `client.market.cancel_and_place(params)` -- строки 128-141 (атомарная cancel+place)

**Открытые ордера:**

- `client.market.get_subaccount_open_orders(product_id, sender)` -- [nado_protocol/client/apis/market/query.py](Nado-grid/nado-python-sdk/nado_protocol/client/apis/market/query.py)

**Book info (tick size, min size):**

- `client.market.get_all_engine_markets()` -- возвращает `AllProductsData` с `perp_products[].book_info` (поля: `price_increment_x18`, `size_increment`, `min_size`)

**Округление цен:**

- `round_x18(x, y)` = `x - x % y` -- [nado_protocol/utils/math.py](Nado-grid/nado-python-sdk/nado_protocol/utils/math.py) строка 91-93 (только вниз!)
- Для sell (округление вверх) нужна кастомная функция: `x + (y - x % y) % y`

**Digest ордера (для отмены):**

- `client.context.engine_client.get_order_digest(order, product_id)` -- см. [sanity/nado_client.py](Nado-grid/nado-python-sdk/sanity/nado_client.py) строки 150-152

**Инициализация клиента:**

- `create_nado_client(mode='mainnet', signer=private_key)` -- [nado_protocol/client/**init**.py](Nado-grid/nado-python-sdk/nado_protocol/client/__init__.py)

**Важные утилиты:**

- `gen_order_nonce()` -- [nado_protocol/utils/nonce.py](Nado-grid/nado-python-sdk/nado_protocol/utils/nonce.py)
- `get_expiration_timestamp(seconds)` -- [nado_protocol/utils/expiration.py](Nado-grid/nado-python-sdk/nado_protocol/utils/expiration.py)
- `subaccount_to_hex()`, `subaccount_to_bytes32()` -- [nado_protocol/utils/bytes32.py](Nado-grid/nado-python-sdk/nado_protocol/utils/bytes32.py)
- `SubaccountParams(subaccount_owner, subaccount_name)` -- [nado_protocol/utils/subaccount.py](Nado-grid/nado-python-sdk/nado_protocol/utils/subaccount.py)

**Что НЕТ в SDK:**

- `place_orders` (batch) -- типы `PlaceOrdersParams` объявлены, но метод не реализован. Ордера ставим по одному.
- WebSocket/Subscriptions client -- полностью отсутствует, нужна своя реализация через `websockets`.

---

## Структура проекта

```
Nado-grid/
  nado-python-sdk/          # SDK (read-only, не модифицируем)
  bot/
    __init__.py
    config.py               # Загрузка YAML + .env, валидация
    exchange_client.py       # Обертка над SDK (place/cancel/query/prices)
    grid_engine.py           # Генерация уровней, Price(k), план ордеров
    execution.py             # Безопасное размещение: throttle, appendix, error handling
    fills_listener.py        # WebSocket подписка на fill stream + fallback polling
    state_store.py           # SQLite: orders, levels, buffers, paused flag
    logger.py                # Структурные JSON-логи
    cli.py                   # CLI: start, stop, status, pause, resume, dry-run
    main.py                  # Точка входа, оркестрация
  config.yaml               # Конфиг стратегии
  .env                       # NADO_PRIVATE_KEY (не коммитить)
  requirements.txt           # nado-protocol, websockets, pyyaml, python-dotenv
```

---

## Пошаговый план реализации с чекпоинтами

### Шаг 0: Скелет проекта, зависимости, конфиг

**Что делаем:**

- Создать `bot/` директорию со всеми модулями-заглушками
- `requirements.txt`: `nado-protocol`, `websockets`, `pyyaml`, `python-dotenv`
- `config.yaml` по шаблону из ТЗ (секция H)
- `.env` с `NADO_PRIVATE_KEY`
- `bot/config.py`: загрузка YAML + dotenv, dataclass `BotConfig` с валидацией (`grid_step_pct > 0`, `levels_down > 0`, и т.д.)
- `bot/logger.py`: `structlog` или стандартный `logging` с JSON-форматированием

**Чекпоинт 0:** `python -c "from bot.config import load_config; c = load_config(); print(c)"` -- без ошибок, значения напечатаны.

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"config_loaded","grid_step_pct":0.1,"levels_down":20,"levels_up":20,"product_id":2}
```

---

### Шаг 1: exchange_client -- инициализация SDK, подключение к mainnet

**Что делаем:**

- `bot/exchange_client.py`: класс `ExchangeClient`
  - `__init__`: вызывает `create_nado_client(mode='mainnet', signer=key)`
  - `get_mark_price(product_id) -> int`: вызывает `client.perp.get_prices(product_id)`, возвращает `int(mark_price_x18)`
  - `get_book_info(product_id) -> BookInfo`: из `client.market.get_all_engine_markets()` извлекает `perp_products` по `product_id`, возвращает `price_increment_x18`, `size_increment`, `min_size`
  - `get_open_orders(product_id, sender) -> list`: обертка над `get_subaccount_open_orders`
  - Обработка сетевых ошибок: retry с exponential backoff (3 попытки, base 1s)

**Чекпоинт 1:** Запустить скрипт-проверку:

```python
from bot.exchange_client import ExchangeClient
ec = ExchangeClient(config)
print("mark_price_x18:", ec.get_mark_price(2))
print("book_info:", ec.get_book_info(2))
```

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"sdk_init_success","mode":"mainnet"}
{"ts":"...","level":"INFO","event":"mark_price_fetched","product_id":2,"mark_price_x18":"98500000000000000000000","update_time":"..."}
{"ts":"...","level":"INFO","event":"book_info_fetched","product_id":2,"price_increment_x18":"...","size_increment":"...","min_size":"..."}
```

---

### Шаг 2: exchange_client -- place_order + cancel (одиночный тест-ордер)

**Что делаем:**

- Добавить в `ExchangeClient`:
  - `place_post_only_order(product_id, price_x18, amount, reduce_only=False, order_id=None) -> (digest, response)`
    - Внутри: `OrderParams(sender, priceX18, amount, expiration=get_expiration_timestamp(86400), appendix=build_appendix(POST_ONLY, reduce_only=reduce_only), nonce=gen_order_nonce())`
    - Обработка ошибок: `2008` PostOnlyOrderCrossesBook, `2003` AmountTooSmall, `2004`/`2005` IncrementErrors, `3001` RateLimit
  - `cancel_order(product_id, digest, sender) -> response`
  - `get_order_digest(order, product_id) -> str`

**Чекпоинт 2:** Выставить buy BTC-PERP по цене `mark_price * 0.85`, POST_ONLY, затем отменить.

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"order_placed","product_id":2,"side":"buy","price_x18":"...","amount":"...","digest":"0xabc...","status":"success"}
{"ts":"...","level":"INFO","event":"order_cancelled","digest":"0xabc...","status":"success"}
```

Если получен `2008`: лог `{"level":"WARN","event":"post_only_crosses_book","err_code":2008}` -- ожидаемо, скорректировать цену.

---

### Шаг 3: grid_engine -- генерация уровней (чистая функция)

**Что делаем:**

- `bot/grid_engine.py`:
  - `generate_grid_levels(P0, config, book_info) -> list[GridLevel]`
    - `GridLevel`: dataclass с `k`, `price_x18`, `side` (buy/sell-target)
    - `Price(k) = P0 * (1 + k * grid_step_pct / 100)`
    - Округление buy: `round_down(price, tick)` -- SDK `round_x18`
    - Округление sell: `round_up(price, tick)` -- кастомная: `price + (tick - price % tick) % tick`
    - Фильтрация: `P_low <= price <= P_high`
  - `build_initial_orders(levels, position_amount, order_size) -> list[OrderIntent]`
    - Если позиции нет: только buy на k=-1..-levels_down
    - Если есть long: buy-лестница + reduce-only sell для имеющегося объема

**Чекпоинт 3:** `dry-run` режим:

```
python -m bot.cli dry-run
```

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"grid_levels_generated","P0":"98500...","levels_count":40,"buy_levels":20,"sell_targets":20}
{"ts":"...","level":"INFO","event":"dry_run_order","k":-1,"side":"buy","price_x18":"98405...","amount":"..."}
{"ts":"...","level":"INFO","event":"dry_run_order","k":-2,"side":"buy","price_x18":"98306...","amount":"..."}
...
{"ts":"...","level":"INFO","event":"dry_run_summary","total_buy_orders":20,"total_notional_x18":"..."}
```

Проверки:

- Цены строго монотонно убывают по k
- Все цены кратны `price_increment_x18`
- Все цены внутри `[P_low, P_high]`

---

### Шаг 4: state_store -- SQLite для хранения состояния

**Что делаем:**

- `bot/state_store.py`: класс `StateStore(db_path)`
  - Таблица `grid_params`: `P0`, `grid_step_pct`, `levels_down`, `levels_up`, `created_at`
  - Таблица `orders`: `order_digest` PK, `k`, `side`, `price_x18`, `qty_total_x18`, `qty_remaining_x18`, `status` (active/filled/cancelled/external), `created_at`, `updated_at`
  - Таблица `pending_buffers`: `k`, `side`, `pending_qty_x18` -- буфер для недовыставленных частичных объемов
  - Таблица `fills_log`: `fill_id`, `order_digest`, `filled_qty`, `remaining_qty`, `price`, `is_taker`, `fee`, `ts`
  - Поле `bot_state`: RUNNING / PAUSED
  - Все записи через транзакции

**Чекпоинт 4:** Юнит-тест: создать store, добавить ордер, обновить remaining, прочитать -- данные совпадают.

---

### Шаг 5: execution -- выставление стартовой buy-сетки

**Что делаем:**

- `bot/execution.py`: класс `OrderExecutor(exchange_client, state_store, config)`
  - `place_initial_grid(levels) -> int`: размещает buy-ордера по уровням, с throttle (пауза ~200ms между ордерами чтобы не попасть в rate limit 5/10sec)
  - Для каждого ордера:
    1. `exchange_client.place_post_only_order(...)`
    2. Если success: сохранить digest в `state_store.orders`
    3. Если `2008`: лог WARNING, пропустить уровень
    4. Если `3001`: backoff 2^n секунд, retry до 5 раз
    5. Если `2003`/`2004`/`2005`: лог ERROR, пропустить (ошибка конфига)
  - После всех: `exchange_client.get_open_orders(...)` и сверить с `state_store`

**Чекпоинт 5:** Запуск на mainnet:

```
python -m bot.cli start
```

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"grid_initial_placing","total_orders":20}
{"ts":"...","level":"INFO","event":"order_placed","k":-1,"side":"buy","price_x18":"...","digest":"0x...","status":"success"}
{"ts":"...","level":"INFO","event":"order_placed","k":-2,"side":"buy","price_x18":"...","digest":"0x...","status":"success"}
...
{"ts":"...","level":"INFO","event":"grid_initial_complete","placed":20,"failed":0}
{"ts":"...","level":"INFO","event":"open_orders_reconciled","expected":20,"actual":20,"diff":0}
```

---

### Шаг 6: fills_listener -- WebSocket подписка на fill

**Что делаем:**

- `bot/fills_listener.py`: класс `FillsListener`
  - Подключение к WSS endpoint (mainnet: `wss://gateway.nado.xyz/v2/ws`)
  - `permessage-deflate` через `websockets` (параметр `extensions`)
  - Ping каждые 30 секунд
  - Subscribe: `{"method":"subscribe","stream":{"type":"fill","product_id":2,"subaccount":"0x..."},"id":1}`
  - На каждое сообщение:
    - Парсить JSON, извлечь `order_digest`, `filled_qty`, `remaining_qty`, `is_taker`, `fee`, `is_bid`, `price`
    - Если `is_taker == true` --> CRITICAL log + set `bot_state = PAUSED`
    - Иначе: обновить `state_store.orders` (remaining_qty), записать в `fills_log`
    - Вызвать callback для refill-логики
  - Reconnect при disconnect: backoff 1s, 2s, 4s... до 60s max
  - Ротация соединения каждые 11 часов (лимит 12h)
- Fallback polling (если WS выключен в конфиге):
  - Каждые `poll_interval_sec`: запросить open_orders, сравнить remaining с прошлым состоянием, определить fills

**Чекпоинт 6:** Подключение к WS, подписка, ожидание fill.

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"ws_connected","endpoint":"wss://gateway.nado.xyz/v2/ws"}
{"ts":"...","level":"INFO","event":"ws_subscribed","stream":"fill","product_id":2}
{"ts":"...","level":"DEBUG","event":"ws_ping_sent"}
... (при тестовом fill)
{"ts":"...","level":"INFO","event":"fill_received","order_digest":"0x...","filled_qty":"...","remaining_qty":"...","is_taker":false,"fee":"...","is_bid":true}
```

---

### Шаг 7: refill-логика (ядро бота)

**Что делаем:**

- В `bot/grid_engine.py` добавить:
  - `on_buy_fill(k_buy, filled_qty) -> OrderIntent`:
    - sell на `k_tp = k_buy + 1`, POST_ONLY + reduce_only, размер = filled_qty (или из буфера)
  - `on_sell_fill(k_sell, filled_qty) -> OrderIntent`:
    - buy на `k_rebuy = k_sell - 1`, POST_ONLY (без reduce_only), размер = filled_qty
  - Буферизация частичных исполнений:
    - Если `filled_qty < min_size` или не кратен `size_increment`: добавить в `pending_buffers[k]`
    - При каждом новом fill: `pending += filled_qty`, пытаться выставить `floor(pending / size_increment) * size_increment`
    - Если сервер возвращает `2003`: оставить в буфере
- В `bot/execution.py`:
  - `handle_fill(fill_event)`: определить k по digest из state, вызвать refill, выставить новый ордер
- Проверка выхода за диапазон: если `Price(k_tp) > P_high` или `Price(k_rebuy) < P_low` --> WARNING лог, ордер не ставится

**Чекпоинт 7:** Контролируемый сценарий:

1. Buy на k=-1 исполняется (рынок движется вниз)
2. Бот ставит sell на k=0 (reduce_only + POST_ONLY)
3. Sell на k=0 исполняется (рынок возвращается)
4. Бот восстанавливает buy на k=-1

**Ожидаемый лог:**

```
{"ts":"...","level":"INFO","event":"fill_received","order_digest":"0x...","k":-1,"side":"buy","filled_qty":"...","is_taker":false}
{"ts":"...","level":"INFO","event":"refill_sell_placed","k_tp":0,"price_x18":"...","qty":"...","reduce_only":true,"digest":"0x..."}
... (sell заполняется)
{"ts":"...","level":"INFO","event":"fill_received","order_digest":"0x...","k":0,"side":"sell","filled_qty":"...","is_taker":false}
{"ts":"...","level":"INFO","event":"refill_buy_placed","k_rebuy":-1,"price_x18":"...","qty":"...","digest":"0x..."}
```

---

### Шаг 8: graceful shutdown, restart, reconcile

**Что делаем:**

- `bot/main.py`:
  - SIGINT/SIGTERM handler: остановить fills_listener, записать state_store, НЕ отменять ордера
  - При старте:
    1. Загрузить state из SQLite
    2. Запросить open_orders с биржи
    3. Reconcile: сопоставить digest из state с биржевыми
    4. "Лишние" (на бирже, но не в state) -- пометить как `external`, не трогать
    5. "Пропавшие" (в state active, но нет на бирже) -- пометить как filled/cancelled, проверить fills
    6. Довыставить недостающие уровни
- CLI-команды:
  - `start` -- полный цикл
  - `stop` -- graceful shutdown
  - `pause` -- `bot_state = PAUSED`, WS продолжает слушать
  - `resume` -- `bot_state = RUNNING`
  - `status` -- вывести: текущие ордера, позиция, P0, mark price, bot_state
  - `dry-run` -- всё кроме place/cancel (только print)

**Чекпоинт 8:**

1. Запустить бот, подождать fill, остановить (Ctrl+C)
2. Перезапустить -- лог `reconcile_diff` показывает 0 новых дублей
3. Ордера на бирже не изменились

**Ожидаемый лог (restart):**

```
{"ts":"...","level":"INFO","event":"state_loaded","orders_in_state":22,"bot_state":"RUNNING"}
{"ts":"...","level":"INFO","event":"reconcile_start","open_orders_exchange":20,"orders_in_state":22}
{"ts":"...","level":"INFO","event":"reconcile_complete","matched":20,"missing_from_exchange":2,"unknown_on_exchange":0}
{"ts":"...","level":"INFO","event":"missing_orders_marked_filled","count":2}
```

---

## Критические инварианты (проверять на каждом шаге)

1. **Все ордера -- POST_ONLY.** Ни один ордер не создается без `build_appendix(OrderType.POST_ONLY, ...)`.
2. **Все sell -- reduce_only.** `build_appendix(OrderType.POST_ONLY, reduce_only=True)` для sell.
3. **Цены кратны tick.** `price_x18 % price_increment_x18 == 0` -- assert перед каждым place.
4. **Размеры кратны size_increment.** `abs(amount) % size_increment == 0` -- assert.
5. **is_taker == true --> PAUSED.** Никаких исключений.
6. **Нет дублей по level k.** State хранит не более одного active ордера на k/side.
7. **Нет ордеров за пределами диапазона.** `P_low <= price <= P_high` -- assert.

