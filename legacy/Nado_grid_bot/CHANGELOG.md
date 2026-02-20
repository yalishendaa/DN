# Changelog (work log)

## 2026-02-08

### 22:15 — Тестовые скрипты и документация для проверки
- Scope: Добавлены скрипты для безопасной проверки работоспособности и подробная документация
- Files:
  - test_connection.py (новый)
  - test_single_order.py (новый)
  - TESTING.md (новый)
  - QUICK_START.md (новый)
- Changes:
  - test_connection.py: read-only проверка подключения к mainnet (mark price, book info, позиция, открытые ордера)
  - test_single_order.py: безопасный тест размещения одного POST_ONLY ордера с автоматической отменой
  - TESTING.md: пошаговая инструкция от подключения до полного запуска, troubleshooting, мониторинг
  - QUICK_START.md: краткая шпаргалка для быстрого старта
- Notes:
  - Все тесты используют реальный ключ из .env, но test_connection.py только читает данные (безопасно)
  - test_single_order.py размещает реальный ордер, но автоматически отменяет его через 2 секунды
  - Dry-run полностью безопасен — не размещает реальные ордера
- Verify:
  - `python test_connection.py` — проверяет подключение без размещения ордеров
  - `python test_single_order.py` — размещает и отменяет один тестовый ордер (требует подтверждения)

### 22:10 — Полная реализация Steps 4-8 + bugfix grid k=0
- Scope: Реализация модулей state_store, execution, fills_listener, main, cli по плану. Исправление grid_engine (k=0 sell_target).
- Files:
  - bot/state_store.py (новый)
  - bot/execution.py (новый)
  - bot/fills_listener.py (новый)
  - bot/main.py (новый)
  - bot/cli.py (новый)
  - bot/__main__.py (новый)
  - bot/grid_engine.py (исправлен)
  - CHANGELOG.md
- Changes:
  - state_store.py: SQLite хранилище с таблицами grid_params, orders (PK=digest), pending_buffers (PK=k+side), fills_log, bot_state; WAL mode, все через транзакции
  - execution.py: OrderExecutor — place_initial_grid с throttle 250ms, _place_with_retry с обработкой кодов 2003/2004/2005/2008/3001, handle_fill с taker→PAUSED, reconcile (сверка local state vs exchange)
  - fills_listener.py: FillsListener (async WebSocket, permessage-deflate, ping 25s, reconnect backoff 1→60s, lifetime rotation 11h) + PollingFillsListener (sync fallback polling)
  - main.py: GridBot оркестратор — init → load/create grid → reconcile → place missing → listen. Graceful shutdown по SIGINT/SIGTERM (ордера не отменяются!)
  - cli.py: CLI с подкомандами start, stop, pause, resume, status, dry-run. __main__.py для python -m bot
  - grid_engine.py: Исправлен баг — k=0 теперь включён как sell_target (раньше пропускался, что ломало refill k=-1→sell k=0)
- Notes:
  - WS endpoint деривируется из gateway URL: wss://gateway.prod.nado.xyz/v1/ws для mainnet
  - Polling fallback определяет fills по дельте unfilled_amount (не знает price/fee — берёт is_taker=False)
  - Dry-run протестирован на mainnet: mark_price $70,735, 20 buy levels сгенерированы корректно
  - Все модули проходят синтаксис-проверку и импортируются без ошибок
- Verify:
  - `PYTHONPATH=nado-python-sdk:. python -m bot.cli dry-run` — 20 buy-ордеров напечатаны, цены монотонные, кратны tick
  - `PYTHONPATH=nado-python-sdk:. python -m bot.cli status` — показывает bot_state, mark_price, active_orders
  - Unit test state_store: upsert/update/read orders, pending buffers, fills log — всё сходится

### 22:00 — Шаги 0-3: Скелет проекта, config, logger, exchange_client, grid_engine
- Scope: Базовый скелет проекта и первые 4 модуля бота
- Files:
  - requirements.txt
  - config.yaml
  - .env.example
  - bot/__init__.py
  - bot/config.py
  - bot/logger.py
  - bot/exchange_client.py
  - bot/grid_engine.py
- Changes:
  - config.py: загрузка YAML + .env, dataclass BotConfig с валидацией (grid_step > 0, breakeven warning)
  - logger.py: JSON-structured логи с полным набором полей (ts, level, event, product_id, digest, k, side, etc.)
  - exchange_client.py: обертка над SDK — get_mark_price, get_book_info, place_post_only_order, cancel_order, retry_on_rate_limit, round_up_x18
  - grid_engine.py: generate_grid_levels (P0-centered, round_down buy / round_up sell), build_initial_orders, on_buy_fill/on_sell_fill с буферизацией
- Notes:
  - SDK не содержит batch place_orders — ордера ставятся по одному с throttle 250ms
  - SDK не содержит WebSocket клиент — реализован кастомный через websockets
  - .env содержит тестовый ключ (hardhat default) — заменить на реальный перед mainnet
  - Поле amount для BTC-PERP интерпретировано как base qty (BTC) в x18 — требует верификации на mainnet
- Verify:
  - `python -c "from bot.config import load_config; print(load_config())"` — конфиг загружается без ошибок
