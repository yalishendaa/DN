# ТЗ и план реализации BTC‑PERP long‑grid‑бота на Nado

## Резюме и границы

**A) Краткое резюме (что будет построено)**  
Будет построен терминальный (CLI) бот сеточной торговли (grid) для **перпетуалов** по **BTC‑PERP** на Nado, работающий на **mainnet**. Идентификатор инструмента в документации: **`BTC-PERP` → `product_id: 2`**. citeturn5view0  
Опорная цена для математики сетки — **mark price**: бот будет получать **`mark_price_x18`** (и **`index_price_x18`**) через официальный Python SDK методом **`client.perp.get_prices(product_id)`**. citeturn19view3  

Торговая логика: **long‑only**, лимитные ордера **только post‑only (maker‑only)**, без постоянного «переезда»/репрайса ордеров; «дозаправка» (replenish/refill) происходит **только по факту исполнений (fills)** через WebSocket‑события. Формат подписки на поток **`fill`** и схема событий **`fill`** описаны в документации Subscriptions. citeturn26view0turn9view0  

**Что бот делать НЕ будет (явно)**  
- Не будет автоматически расширять/сдвигать сетку и не будет предпринимать действий при выходе цены за диапазон (только логирование и ожидание ручного решения).  
- Не будет использовать рыночные ордера и не будет пытаться «догонять» цену репрайсами.  
- Не будет автоматизировать депозиты/выводы.  
- Не будет делать бэктестинг.  
- Параметры «плеча» для перпов как отдельная настройка в API/SDK **в явном виде не подтверждены документацией** — это фиксируется как «не найдено в документации» и учитывается в разделе неизвестных. citeturn11view1turn22view0turn19view3  

## Что говорят документы Nado и Python SDK

**B) Что говорится в документации (с привязкой к точным разделам)**

### Аутентификация, ключи, подпись, заголовки, секреты

- **Модель авторизации для торговых действий (executes): EIP‑712 подпись.** Документация Gateway прямо говорит, что «All executes are signed using EIP712», и описывает, что каждый execute содержит структурированные данные и подпись. citeturn11view0  
- **Домен EIP‑712** (name/version/chainId/verifyingContract) и правило выбора `verifyingContract`:  
  - для **place order** — использовать адрес, зависящий от `product_id` (пример в доке);  
  - для «всего остального» — адрес endpoint. citeturn11view0  
- **Подпись для подписочных (Subscriptions) аутентифицированных потоков** выполняется через `method: "authenticate"` и объект `tx` со структурой `StreamAuthentication { sender, expiration }`, где `expiration` — **миллисекунды Unix epoch**. Ограничение: запросы будут отклонены, если `expiration` меньше текущего времени или больше чем на 100 секунд вперед. citeturn10view1  
- **Секреты в Python SDK:** функция **`create_nado_client(mode, signer=None, ...)`** принимает `signer` как **`LocalAccount` или строку приватного ключа**. Это означает, что проект должен хранить приватный ключ локально (например, в `.env` на VPS), но **точные имена переменных окружения в документации не заданы**. citeturn25view0turn15view0  
- **Требуемые заголовки для Gateway:** для Gateway API указано, что HTTP‑запросы должны иметь `Accept-Encoding` с `gzip`, `br` или `deflate`. citeturn2view3  
- **Подключение к Subscriptions WebSocket и поддержка компрессии/keepalive:** требуется поддержка `permessage-deflate` (через `Sec-WebSocket-Extensions`) и отправка ping‑кадров **каждые 30 секунд**, иначе соединение закрывается; также указано ограничение на длительность соединения (12 часов). citeturn1view6  

### Рыночные данные: mark price и спецификация инструмента

- **Mark price / index price через SDK:** метод **`client.perp.get_prices(product_id)`** возвращает `index_price_x18`, `mark_price_x18`, `update_time` (все поля перечислены в API Reference SDK). citeturn19view3  
- **Формат цен и количеств:** в Gateway Queries указано, что цены идут в формате **x18** (умножение на `10^18`), а количества/балансы «нормализованы к 18 десятичным». citeturn6view2  
- **Спецификация продукта (шаг цены/шаг размера/минимальный размер):** запрос **All Products** возвращает `book_info` и перечисляет поля:  
  - `size_increment` — кратность размера (base units),  
  - `price_increment_x18` — минимальный шаг цены (x18),  
  - `min_size` — минимум (в описании указан минимум в quote units USDT0, x18, пример 100 USDT0). citeturn7view0  
- **Сопоставление символа и `product_id`:** через Symbols query (REST `GET [GATEWAY_REST_ENDPOINT]/symbols`) показано отображение: `BTC-PERP` имеет `product_id: 2`. citeturn5view0  

### Размещение ордеров: точные методы SDK и точные поля

- **Размещение ордера через SDK:** пример Getting Started показывает создание `OrderParams(...)` и вызов **`client.market.place_order(PlaceOrderParams(product_id=..., order=...))`**. citeturn15view0turn22view0  
- **Параметры ордера в SDK:**  
  - `OrderParams` имеет `priceX18` (int), `amount` (int, положительное = buy/long, отрицательное = sell/short), `expiration` (unix timestamp), `nonce` (optional), `appendix` (int). citeturn22view0  
  - `PlaceOrderParams` содержит `product_id`, `order`, опциональные `id`, `digest`, `spot_leverage`. citeturn22view0  
- **Эквивалент полей на уровне Gateway Place Order:** в документации Place Order формат запроса содержит `product_id` и `order { sender, priceX18, amount, expiration, nonce, appendix }` плюс `signature`, и опционально `spot_leverage`, `id`. citeturn11view1  
- **Maker‑only / post‑only флаг:** order appendix описывает, что `Order Type` включает значение **`POST_ONLY`** (битовая раскладка: `3 = POST_ONLY`, «reject if would take liquidity»). Это — документированный механизм «не стать тейкером». citeturn11view1  
  - В SDK‑примере используется **`build_appendix(OrderType.POST_ONLY)`** при создании ордера. citeturn15view0  
- **Reduce‑only флаг:** в описании order appendix есть флаг **Reduce Only (бит 11)** — «only decreases an existing position». citeturn11view1  
  - В User Reference SDK есть пример построения appendix с `reduce_only=True`. citeturn4view2  
- **Batch‑размещение (`place_orders`) в Gateway:** существует endpoint **Place Orders**, где за один запрос передается массив `orders[]`, есть флаг `stop_on_failure`, и описана «processing penalty 50ms» на запрос. citeturn26view2  
  - **Конфликт/разрыв с SDK:** в API Reference SDK в `MarketExecuteAPI` перечислен `place_order`, но **метод `place_orders` как отдельный SDK‑метод в списке не показан** — это влияет на способ массового выставления сетки (см. раздел «Неизвестные»). citeturn16view0turn19view0turn26view2  

### Жизненный цикл ордера: открытые ордера, отмена, сделки/исполнения, частичные исполнения

- **Просмотр открытых ордеров через SDK:** Getting Started показывает **`client.market.get_subaccount_open_orders(product_id, sender)`**. citeturn15view0turn19view2  
- **Digest ордера и отмена:**  
  - Getting Started: для отмены нужен order digest; digest получают через **`client.context.engine_client.get_order_digest(order, product_id)`**. citeturn15view0  
  - Отмена выполняется через **`client.market.cancel_orders(CancelOrdersParams(productIds=[...], digests=[...], sender=...))`**. citeturn15view0turn22view1turn19view0  
  - На уровне Gateway существует execute **Cancel Orders** (док‑страница), но специфические поля/ответы должны сверяться с ней при реализации низкоуровневых веток. citeturn11view3  
- **Частичные исполнения и отслеживание остатка:**  
  - Событие `order_update` содержит поле `amount` как «Remaining unfilled amount (x18)», и прямо сказано «check amount to see how much remains» при `reason: "filled"`. citeturn9view0  
  - Событие `fill` содержит `filled_qty`, `remaining_qty`, `original_qty`, `price`, `is_taker`, `fee` и явно описывает, что один ордер может породить несколько `fill`‑событий; рекомендуется группировать по `order_digest` и использовать `remaining_qty`. citeturn9view0  

### Стриминг (WebSocket): наличие, форматы подписки, события, fallback

- **Subscriptions API существует и предназначен для live‑данных без polling:** Streams page описывает «persistent subscriptions», где сервер «push events — no polling required». citeturn26view0  
- **Список доступных потоков и требование аутентификации:**  
  - `order_update` — единственный поток, требующий аутентификации;  
  - `fill` — без аутентификации и дает «execution price and fees». citeturn26view0  
- **Формат запроса подписки:** `{"method":"subscribe","stream":{...},"id":...}`; поля `stream` включают `type`, `product_id` (может быть `null`), `subaccount` (для `fill`/`order_update`/`position_change`), и т.д. citeturn26view0  
- **Формат событий:** формально описан на Events page (пример и табличные поля для `order_update`, `fill`, `position_change` и др.). citeturn9view0  
- **Fallback polling‑подход:** если WebSocket недоступен/нестабилен, документация Gateway поддерживает Queries через REST `GET/POST [GATEWAY_REST_ENDPOINT]/query`, а SDK предоставляет методы получения открытых ордеров и цен. citeturn6view2turn19view2turn19view3  

### Лимиты и ретраи: что найдено в документации

- **Общий принцип rate limits:** Nado использует weight‑based rate limiting, применяется к HTTP и WebSocket сообщениям, лимиты — по окнам 1 минута и 10 секунд; лимитирование по IP, wallet и есть глобальный лимит open orders на subaccount/market. citeturn5view1  
- **Лимит активных ордеров:** максимум **500 открытых ордеров на subaccount на market** (в rate limits overview). citeturn5view1  
- **Place Order лимиты:** указаны разные режимы в зависимости от `spot_leverage` (с leverage быстрее, без leverage — «30 orders/min или 5 orders/10 sec» и weight=20). citeturn11view1  
- **All Products query лимит:** 480 req/min (weight=5). citeturn7view0  
- **Subscriptions rate limits:** максимум **100 активных WebSocket‑соединений на IP**; и **до 5 аутентифицированных соединений на один wallet**, превышение приводит к disconnect. citeturn26view1turn10view1  
- **Ретрай‑гайд / backoff‑рекомендации:** конкретная «рекомендованная стратегия ретраев» в документации **не найдена** (см. раздел неизвестных). citeturn5view1turn5view2  

### Ошибки и коды, важные для сетки и maker‑only

- Документация Errors перечисляет коды (examples):  
  - `2008 PostOnlyOrderCrossesBook` (post‑only пересекает стакан),  
  - `2004 InvalidAmountIncrement`, `2005 InvalidPriceIncrement`,  
  - `2003 OrderAmountTooSmall`,  
  - `2007 OraclePriceDifference`,  
  - `3001 RateLimit`. citeturn5view2  
Эти коды определяют поведение бота при валидации и аварийных ситуациях, особенно для maker‑only enforcement и «правильного округления» под `size_increment/price_increment_x18`. citeturn5view2turn7view0  

### Выявленные конфликты документации и влияние на реализацию

- **Типы полей `priceX18/amount`: SDK vs Gateway.**  
  - В Gateway Place Order поля `priceX18/amount/expiration/nonce/appendix` описаны как строки в JSON примерах и в табличном описании параметров. citeturn11view1  
  - В Python SDK `OrderParams.priceX18` и `amount` описаны как `int`. citeturn22view0  
  **Влияние:** при работе через SDK передаем целые числа (x18), а при прямом REST‑вызове — следуем JSON‑формату документации (строки). Иначе возможны ошибки сериализации/подписи. citeturn11view1turn22view0  

- **Единицы `min_size`: конфликт «quote units» vs проверка по `abs(amount)`.**  
  - В All Products `min_size` описан как **минимальная стоимость заявки в USDT0 (quote), x18**, с примером «100 USDT0 minimum order value». citeturn7view0  
  - В Errors для `2003 OrderAmountTooSmall` текст ошибки сравнивает **`abs(amount)`** с `min_size`. citeturn5view2  
  **Влияние:** перед тем как жестко кодировать локальную проверку min size, нужно экспериментально подтвердить, чему равен `amount` с точки зрения matching engine (base qty или quote value) для BTC‑PERP, либо полагаться на server‑side validation и обрабатывать `2003` как сигнал увеличить размер. В ТЗ это помечается как риск‑неопределенность. citeturn7view0turn5view2  

- **Разные единицы времени для `expiration`:**  
  - `order.expiration` для Place Order — **секунды Unix epoch**. citeturn11view1  
  - `tx.expiration` для Subscriptions Authentication — **миллисекунды Unix epoch**. citeturn10view1  
  **Влияние:** нельзя переиспользовать одно и то же значение; нужно два отдельных генератора времени. citeturn11view1turn10view1  

## Спецификация стратегии сетки long‑only

**C) Спецификация стратегии и детерминированные правила**

### Обозначения и входные параметры

- `product_id`: **2** для `BTC-PERP` (подтверждено Symbols). citeturn5view0  
- `P0`: стартовая **mark price**, полученная через `client.perp.get_prices(2).mark_price_x18`. citeturn19view3  
- Значения цен/количеств внутри API — **x18**. citeturn6view2turn11view1  
- Рыночные ограничения инструмента берутся из `book_info` (как минимум `price_increment_x18`, `size_increment`, `min_size`) через All Products query. citeturn7view0  

**Конфиг стратегии (обязательный минимум):**
- `grid_step_pct` — шаг сетки в процентах (например 0.5).  
- `lower_bound_pct`, `upper_bound_pct` — границы диапазона относительно `P0`.  
- `levels_down` — количество уровней ниже `P0`, на которых будут стоять buy‑ордера.  
- `levels_up` — количество уровней выше `P0`, используемых как цели для sell‑ордера (тейк‑профит), и как уровни, на которых «рефиллится» buy после sell.  
- `order_size` — фиксированный размер на уровень (в терминах `amount` x18; конкретная экономическая интерпретация «в BTC» требует уточнения единиц `amount`, см. конфликт min_size). citeturn11view1turn7view0turn5view2  

### Вычисление границ и уровней

1) На старте бот фиксирует `P0` (mark price). citeturn19view3  

2) Границы диапазона:  
- `P_low = P0 * (1 - lower_bound_pct/100)`  
- `P_high = P0 * (1 + upper_bound_pct/100)`  
(Диапазон статический и не пересчитывается до ручной перезагрузки/перезапуска бота.)

3) Уровни сетки определяются индексом `k`:
- `k ∈ [-levels_down, ..., -1, 0, +1, ..., +levels_up]`
- `Price(k) = P0 * (1 + k * grid_step_pct/100)`

4) **Округление под шаг цены (tick size):**  
Любая вычисленная цена должна округляться к `price_increment_x18` из `book_info`. citeturn7view0turn5view2  
Точный способ округления (вниз/вверх/к ближайшему) фиксируется так:  
- для buy‑ордеров: округлять **вниз** (чтобы гарантировать «не выше ожидаемого»),  
- для sell‑ордеров: округлять **вверх**.  
Если выбранный метод округления приводит к нарушению инкремента, сервер может вернуть `2005 InvalidPriceIncrement`. citeturn5view2turn7view0  

### Какие ордера выставляются при старте

Стартовый расклад зависит от фактической позиции:

- **Если позиции по BTC‑PERP нет (`position_change.amount == 0`)**:  
  - Выставить **только buy‑ордера** на уровнях `k = -1 ... -levels_down`.  
  - Все ордера: **limit + post‑only** (appendix `POST_ONLY`). citeturn11view1turn15view0  
  - Sell‑ордера не ставить (чтобы не рисковать выходом в short). Использование reduce‑only на sell будет включено позже (см. refill). citeturn11view1  

- **Если уже есть long‑позиция (`position_change.amount > 0`)**:  
  - Buy‑лестница выставляется так же.  
  - Дополнительно бот может (детерминированно) разместить reduce‑only sell‑ордера для уже имеющегося объема, разбивая текущую позицию на «лоты» `order_size` и размещая sell от `k=+1` вверх до исчерпания объема или до `k=+levels_up`. Флаг reduce‑only документирован в order appendix. citeturn11view1turn9view0  
  - Если объема позиции меньше одного `order_size`, то остаток либо не хеджируется sell‑ордером, либо агрегируется до достижения минимального размера — это зависит от `min_size/size_increment` (см. обработку частичных/минимумов ниже). citeturn7view0turn5view2  

### Логика refill по факту исполнений без постоянного репрайса

Источник истины по исполнению — событие **`fill`** (реальное время) со схемой `filled_qty`, `remaining_qty`, `is_taker`, `fee`, `price`, `is_bid`. citeturn9view0  

#### Когда исполнился buy (is_bid=true)

На каждое `fill`‑событие по buy‑ордеру:
1) Определить уровень `k_buy`, к которому относился ордер (из состояния бота, т.к. в `fill` нет цены заявки, а есть цена исполнения; привязка должна храниться по `order_digest`). citeturn9view0turn15view0  
2) Рассчитать целевой уровень тейк‑профита: `k_tp = k_buy + 1`.  
3) Создать **sell‑ордер**:
   - цена: `Price(k_tp)` (округление вверх под tick),  
   - размер: **ровно `filled_qty`** (или аккумулированный «буфер исполнений», если `filled_qty` не проходит минимальные ограничения),  
   - appendix: `POST_ONLY` + `reduce_only=True`. citeturn11view1turn4view2turn9view0  
4) **Не отменять** исходный buy‑ордер, если он частично исполнен; он остается в книге с остатком `remaining_qty` (это соответствует семантике `fill`/`order_update`). citeturn9view0  

#### Когда исполнился sell (is_bid=false)

На каждое `fill`‑событие по sell‑ордеру:
1) Определить уровень `k_sell`, к которому относился sell‑ордер (по `order_digest` из state). citeturn9view0turn15view0  
2) Рассчитать рефилл‑уровень buy: `k_rebuy = k_sell - 1`.  
3) Создать **buy‑ордер**:
   - цена: `Price(k_rebuy)` (округление вниз),  
   - размер: `filled_qty` (или `order_size`, если стратегия фиксирует «всегда один лот», но тогда недостающий/лишний объем нужно явно определять — базовая версия использует `filled_qty` как зеркальную величину),  
   - appendix: `POST_ONLY` (reduce‑only не ставится на buy). citeturn11view1turn9view0turn15view0  

#### Частичные исполнения и минимальные ограничения

Проблема: `filled_qty` может быть меньше минимума или не кратно `size_increment`, а сервер требует инкременты и минимумы. citeturn7view0turn5view2  

Детерминированное правило:
- Вести на уровне `k` буфер «недовыставленного» количества `pending_qty_x18` для следующего противоположного ордера.  
- При каждом `fill` добавлять `filled_qty` в буфер и пытаться выставить ордер на **максимально возможную** величину, которая:
  - кратна `size_increment`,  
  - удовлетворяет признакам минимального размера (`min_size`) в той интерпретации, которую фактически принимает движок (из-за конфликта min_size локальная проверка может быть неполной; в этом случае серверная ошибка `2003` ведет к накоплению до следующего шага). citeturn7view0turn5view2  
- Не прошедший объем остается в буфере до следующего fill.

### Жесткое соблюдение maker‑only (post‑only) и реакция на нарушения

Механизм в документации:
- Maker‑only достигается через `POST_ONLY` в order appendix: «reject if would take liquidity». citeturn11view1  
- Ошибка, подтверждающая «почти‑текер»: `2008 PostOnlyOrderCrossesBook`. citeturn5view2  

Контроль исполнения:
- Событие `fill` содержит `is_taker` и `fee`. Это позволяет обнаруживать любые taker‑исполнения даже при post‑only политике. citeturn9view0  

Детерминированное правило реакции:
- Если получен `fill` с `is_taker=true` → событие уровня **CRITICAL** в логах с `order_digest`, `price`, `filled_qty`, `fee` и немедленный перевод бота в режим `PAUSED` (новые ордера не выставляются до ручного вмешательства). Поле `is_taker` документировано. citeturn9view0  

### Математика прибыльности с учетом комиссий

Требование по комиссиям (задано пользователем):  
- maker fee = **0.01%**  
- taker fee = **0.035%**  

Бот ориентирован на post‑only, значит целевой сценарий — **maker+maker** на входе и выходе.

Пусть `f_m = 0.0001` (0.01%), `g` — шаг сетки как доля (например 0.001 = 0.1%). Если buy по цене `P`, а sell по цене `P*(1+g)`, то приближенная нетто‑доходность «round‑trip» при симметричных комиссиях:
\[
R \approx \frac{(1+g)\cdot(1-f_m)}{(1+f_m)} - 1
\]
Порог безубыточности (`R>0`):
\[
g > \frac{1+f_m}{1-f_m}-1 = \frac{2f_m}{1-f_m}
\]
Для `f_m=0.0001`:
\[
g_{\min} \approx \frac{0.0002}{0.9999} \approx 0.00020002 \Rightarrow 0.020002\%
\]
Следовательно, при условии maker‑maker, шаг сетки должен быть **строго больше ~0.0200%**, иначе ожидаемая прибыль «на круг» не покрывает комиссии.  

Если хотя бы одно исполнение становится taker (что бот трактует как критическую аномалию), порог становится существенно выше и должен использовать `f_t=0.00035` (0.035%) в аналогичной формуле — поэтому так важен контроль `is_taker`. Поля `fee` и `is_taker` присутствуют в `fill`. citeturn9view0  

## Управление инвентарём в контексте перпетуалов

**D) Что такое «инвентарь» для long‑grid в перпах и зачем он важен**

### Определение «инвентаря» в этом боте

В контексте перпетуалов «инвентарь» — это, прежде всего:
- **текущий размер позиции** по продукту (для перпов: `amount`, где положительное = long),  
- **связанная с позицией виртуальная квота** (`v_quote_amount` в событии `position_change`), которая отражает стоимость входа/состояние для перп‑учета. citeturn9view0  

Документация Subscriptions `position_change` прямо описывает:
- `amount`: «New position amount… Positive = long, negative = short»,  
- `v_quote_amount`: «For perps … negative of entry cost». citeturn9view0  

### Почему это критично для long‑grid

- Long‑grid на падающем рынке **накапливает позицию**: каждый сработавший buy увеличивает `amount`. citeturn9view0  
- Без контроля «инвентаря» бот может:
  - упереться в лимиты здоровья/маржинальности (механика health/маржи описана в экосистеме Nado, но конкретные лимиты стратегии здесь не задаются),  
  - упереться в лимит размера ордера/отклонение по oracle (ошибка `2007 OraclePriceDifference` ограничивает допустимые цены относительно oracle). citeturn5view2turn7view0  

### Что можно контролировать (без навязывания риск‑правил)

Даже без «жестких риск‑лимитов» бот обязан иметь **управляемые ручки конфигурации**, которые позволяют оператору ограничивать инвентарь:

- `order_size` (размер «лота» на уровень): определяет скорость набора `amount`.  
- `levels_down` и `lower_bound_pct`: определяют потенциальный максимум набора позиции в неблагоприятном движении.  
- `reduce_only` на sell‑ордерах (обязательно): предотвращает случайное открытие short при работе «противоположными» ордерами; флаг reduce‑only документирован в order appendix. citeturn11view1turn4view2  
- Опционально (как настраиваемые, но не включаемые по умолчанию ограничения):  
  - `max_position_qty_x18` — «не выставлять новые buy, если текущая позиция + pending buys превысит лимит».  
  Точная методика вычисления «плеча 1x для перпа» как отдельного параметра **не найдена в документации** (см. раздел неизвестных). citeturn11view1turn22view0turn19view3  

### Плечо 1x и `spot_leverage`

- В Gateway Place Order есть параметр `spot_leverage`: если `false`, размещение проваливается, если транзакция вызывает borrow на subaccount; по умолчанию `true`. citeturn11view1  
- В SDK `PlaceOrderParams` также содержит `spot_leverage` как optional. citeturn22view0  

**Важно:** это поле описано как «spot leverage / borrow»‑контроль. Явного параметра «leverage для perp 1x» в найденных разделах нет — поэтому «1x» в перп‑смысле фиксируется как «не найдено в документации», а проект трактует требование как «не использовать заем (borrow)‑механизмы и не включать изолированные режимы без необходимости». citeturn11view1turn22view0turn11view0  

## Архитектура и модули Python

**E) Архитектура проекта и состав модулей**

Ограничение: официальный Python SDK покрывает REST/HTTP‑клиентов (engine/indexer/market/perp), но **WebSocket Subscriptions в SDK как готовый клиент в API Reference не обнаружен**; значит `fills_listener` должен использовать отдельную WebSocket‑реализацию, строго следуя Subscriptions docs. citeturn16view0turn26view0  

### Модуль exchange_client

**Ответственность:** единая «обертка» над SDK и (минимально) над WebSocket.  
**Входы:** конфиг сети (`mainnet`), приватный ключ, `product_id`, subaccount.  
**Функции (опора на SDK/док):**
- Инициализация клиента через `create_nado_client` с `mode='mainnet'`. citeturn25view0  
- Получение mark price: `client.perp.get_prices(product_id)`. citeturn19view3  
- Получение спецификации инструмента: `client.market.get_all_engine_markets()` (AllProductsData) и извлечение `book_info`. citeturn19view2turn7view0  
- Размещение/отмена: `client.market.place_order(...)`, `client.market.cancel_orders(...)`. citeturn15view0turn19view0turn22view1  
- Получение открытых ордеров: `client.market.get_subaccount_open_orders(...)`. citeturn15view0turn19view2  

**Режимы отказа:** сеть/таймауты, rate limit (`3001`), ошибки инкрементов/минимумов (`2003–2005`), post‑only пересечение (`2008`). citeturn5view2  

### Модуль config

**Ответственность:** загрузка единственного конфиг‑файла (YAML/JSON) + `.env`, валидация ключей.  
**Отказ:** отсутствуют поля, некорректные числа (`grid_step_pct<=0`, границы).  

### Модуль grid_engine

**Ответственность:**  
- детерминированная генерация уровней `k` и цен `Price(k)` на основе `P0`, step и границ;  
- округление согласно `price_increment_x18`;  
- построение «плана ордеров» (какие buy ставим сейчас, какие sell появятся после fills).  

**Входы:** `P0`, `book_info`, конфиг уровней. citeturn7view0turn19view3  
**Выходы:** набор команд «place/cancel» для execution‑слоя.  

### Модуль execution

**Ответственность:**  
- безопасное размещение post‑only ордеров;  
- обязательный reduce‑only для sell;  
- троттлинг под rate limits;  
- обработка ошибок (код → действие). citeturn11view1turn5view1turn5view2  

**Maker‑only enforcement:**  
- appendix `POST_ONLY`;  
- контроль `fill.is_taker`. citeturn11view1turn9view0  

### Модуль fills_listener

**Ответственность:** подписка на WebSocket поток `fill` (и опционально `order_update`) и доставка событий в `grid_engine`.  
- Подписка на `fill`: сообщение `{"method":"subscribe","stream":{"type":"fill","product_id":2,"subaccount":"0x..."},"id":...}`. citeturn26view0  
- Схема события `fill`: поля `order_digest`, `filled_qty`, `remaining_qty`, `is_taker`, `fee` и др. citeturn9view0  
- Требования к соединению: `permessage-deflate`, ping каждые 30s, лимит 12h. citeturn1view6turn26view1  
- Аутентификация нужна только для `order_update`. citeturn26view0turn10view1  

**Fallback (если WS недоступен):** polling открытых ордеров + периодический `get_prices` (mark) и сверка изменений. citeturn19view2turn19view3  

### Модуль state_store

**Цель:** безопасный рестарт без “двойных” ордеров и потерянных привязок `digest ↔ уровень k`.  

Варианты:
- **JSON файл:** проще, но хуже для конкурентного доступа/атомарности при частых событиях.  
- **SQLite:** лучше для транзакционной целостности, проще делать «reconcile» по таблицам (orders, fills, buffers).  

Детерминированный минимум данных:
- `P0` и параметры сетки,  
- таблица `orders`: `order_digest`, `side`, `k`, `qty_total_x18`, `qty_remaining_x18`, `status`,  
- буферы `pending_qty_x18` по уровням,  
- отметка «PAUSED» при нарушениях maker‑only.

### Модуль logger

**Структурные логи:** JSON‑строки или ключ=значение (CLI‑friendly).  
Критические алерты — в логах уровня CRITICAL (см. `is_taker=true`, disconnect WS, rate limit storm). citeturn9view0turn26view1turn5view2  

### Модуль cli

Команды: `start`, `stop`, `status`, `pause`, `resume`, `dry-run`.  
Поддержка `dry-run` как «не отправлять place/cancel, только печатать план» — **не опирается на специальный режим API (“validate only”)**, т.к. он **не найден в документации**. citeturn11view1turn26view2  

## План реализации и проверки на VPS

**F) Пошаговый план (инкрементальная сборка с чекпоинтами)**  
Цель — собирать систему по частям, проверяя каждый слой «в бою» маленькими, безопасными действиями.

### Шаг 0: Подготовка VPS, окружение, зависимости, секреты

**Действия:**
- Установить Python версии **3.9+** (требование SDK). citeturn14search0  
- Создать `venv`, установить пакет SDK через `pip install nado-protocol`. citeturn14search0  
- Добавить зависимости для WebSocket клиента (например, `websockets`) — конкретный пакет не регламентирован документацией Nado, это реализационная деталь. citeturn26view0turn1view6  
- Организовать хранение приватного ключа (например `.env` + права 600). SDK принимает приватный ключ строкой как `signer`. citeturn25view0turn15view0  

**Definition of Done:**
- `python --version` показывает 3.9+.
- Установка `nado-protocol` успешна.
- Приватный ключ не попадает в логи.

### Шаг 1: Инициализация клиента mainnet и базовый «живой» запрос

**Действия:**
- Создать клиента через `create_nado_client(mode='mainnet', signer=<private_key>)`. Значение `MAINNET = 'mainnet'` определено в `NadoClientMode`. citeturn25view0  
- Выполнить легкий запрос, подтверждающий работоспособность engine/indexer связки, например:
  - `client.market.get_all_engine_markets()` (возвращает состояние рынков/продуктов). citeturn19view2  

**Definition of Done:**
- В логах: `sdk_init_success=true`, `mode=mainnet`.
- В логах: успешный ответ (без `status=failure`) на запрос к рынкам.

### Шаг 2: Получение BTC‑PERP спецификаций и mark price, первичная валидация

**Действия:**
- Подтвердить `product_id` для `BTC-PERP` через Symbols (встроенным путем SDK или зафиксировать `2` из доков). Документированное значение: `BTC-PERP → 2`. citeturn5view0  
- Получить `book_info` для `product_id=2` через All Products (через SDK `get_all_engine_markets()` → `AllProductsData.perp_products[].book_info`). citeturn19view2turn7view0  
- Получить `mark_price_x18` через `client.perp.get_prices(2)`. citeturn19view3  
- Сохранить в логи: `mark_price_x18`, `price_increment_x18`, `size_increment`, `min_size` (как значения из ответа). citeturn7view0turn19view3  

**Definition of Done:**
- В логах отражены: `product_id=2`, `symbol=BTC-PERP`, `mark_price_x18` и `book_info`. citeturn5view0turn7view0turn19view3  
- Локальная проверка: `price_increment_x18 > 0`, `size_increment > 0` (если равны 0 — это повод остановиться, т.к. ордера невозможно валидировать). Поля определены в `book_info`. citeturn7view0  

### Шаг 3: Безопасный тест‑ордер post‑only и отмена

**Действия:**
- Собрать `OrderParams` и `PlaceOrderParams` как в Getting Started, но для `product_id=2`. citeturn15view0turn22view0  
- Обязательно поставить `appendix = build_appendix(OrderType.POST_ONLY)` (maker‑only). citeturn15view0turn11view1  
- Выбрать цену так, чтобы post‑only не пересек стакан (иначе ожидаема ошибка `2008`). Код ошибки задокументирован. citeturn5view2  
- Сразу после подтверждения «ордер стоит» — вычислить digest (как в Getting Started) и отменить через `cancel_orders`. citeturn15view0turn22view1turn19view0  

**Definition of Done:**
- Ордер успешно размещается и затем отменяется.
- В логах есть `order_digest` и результат cancel.
- Если получена ошибка `2008 PostOnlyOrderCrossesBook` — тест считается успешным подтверждением maker‑only поведения; нужно скорректировать цену ниже/выше. citeturn5view2turn11view1  

### Шаг 4: Генератор сетки как чистая функция + юнит‑проверки

**Действия:**
- Реализовать функцию `generate_levels(P0, step_pct, bounds, levels_down, levels_up, book_info)`:
  - строит список уровней `k` и цен,  
  - округляет цены к `price_increment_x18`. citeturn7view0turn5view2  
- Проверки:
  - цены монотонны по k,
  - все цены в границах `[P_low, P_high]`,
  - все цены кратны `price_increment_x18`.

**Definition of Done:**
- Запуск `dry-run` печатает уровни и итоговый список buy‑уровней.
- Для случайного набора параметров функция не выдает уровни вне границ.

### Шаг 5: Выставление стартовой buy‑сетки и сверка открытых ордеров

**Действия:**
- Для каждого `k=-1..-levels_down` выставить buy‑ордер:
  - appendix: POST_ONLY,  
  - `amount > 0` (long/buy), что согласуется с правилом Place Order: positive amount = buy. citeturn11view1turn22view0  
- Из‑за лимитов `Place Order` без leverage (weight=20) обязательно ввести троттлинг (например пауза между ордерами), иначе возможен `3001 RateLimit`. citeturn11view1turn5view2turn5view1  
- После выставления — получить `open_orders` через `get_subaccount_open_orders` и сверить, что число ордеров совпадает с ожидаемым. citeturn15view0turn19view2  

**Definition of Done:**
- Открытые ордера по продукту показывают ожидаемый набор.
- В логах: `grid_initial_orders_placed=N`, `open_orders_count=N`.

### Шаг 6: Подписка на fills через WebSocket и обработка частичных исполнений

**Действия:**
- Подключиться к mainnet Subscriptions endpoint (WSS URL берется из Endpoints). citeturn3view0  
- Подписаться на `fill` stream для `product_id=2` и нужного `subaccount`. Формат подписки описан в Streams. citeturn26view0  
- На каждое событие `fill`:
  - обновить state по `order_digest`, `filled_qty`, `remaining_qty`;  
  - зафиксировать `fee`, `is_taker`; если `is_taker=true` → CRITICAL и `PAUSED`. citeturn9view0  

**Definition of Done:**
- В логах: `ws_connected=true`, `subscribed_stream=fill`.
- При тестовом исполнении (вручную, обнулением цены/объемом рынка) бот фиксирует `fill` и корректно обновляет `remaining_qty`.
- Нет падений при серии `fill` для одного и того же `order_digest` (док прямо описывает, что это возможно). citeturn9view0  

### Шаг 7: Реализация refill‑логики и «контролируемый сценарий» проверки

**Действия:**
- При buy fill — размещать reduce‑only sell на `k+1`. Reduce‑only описан в appendix. citeturn11view1turn4view2  
- При sell fill — размещать buy на `k-1`.  
- Все новые ордера — post‑only. citeturn11view1turn15view0  
- Ввести «сценарий проверки»:  
  - Оператор вручную добивается исполнения одного buy‑ордера (например, рыночным движением цены),  
  - бот ставит sell,  
  - затем оператор добивается исполнения sell,  
  - бот восстанавливает buy.

**Definition of Done:**
- На buy fill появляется соответствующий sell (и он reduce‑only).
- На sell fill появляется соответствующий buy.
- Количество «постоянных» ордеров остается в ожидаемых рамках (не происходит лавинообразного роста; буфер частичных исполнений не создает тысячи ордеров).

### Шаг 8: Корректное завершение, рестарт и reconcile

**Действия:**
- Реализовать graceful shutdown:
  - остановить прием WS‑сообщений,
  - записать state_store,
  - не отменять ордера автоматически (соответствует требованию «не делать ничего автоматически при выходе из диапазона/остановке», если оператор не дал команду cancel).  
- Реализовать рестарт:
  - загрузить state_store,
  - запросить `open_orders` и сверить с локальной таблицей,
  - для «неизвестных» digest (есть на бирже, но нет в state) — пометить как `external` и не трогать без флага `--adopt`. citeturn19view2  

**Definition of Done:**
- Рестарт не создает дубликаты ордеров.
- Логи содержат `reconcile_diff` (сколько ордеров совпало/лишние/отсутствующие).

## Операционное руководство и конфигурация

**G) Операционный плейбук**

### Запуск, остановка, пауза, возобновление
- `start`: инициализация SDK клиента (`mainnet`), загрузка state, получение `P0` и `book_info`, размещение недостающих ордеров. citeturn25view0turn19view3turn7view0  
- `pause`: прекращение выставления новых ордеров, WS‑подписка может оставаться (только логируем fills).  
- `resume`: возвращение к refill‑логике.  
- `stop`: graceful shutdown (без автоснятия ордеров, если не указано явно).

### Если цена вышла за диапазон
Требование: «ничего не делать автоматически».  
Детерминированное поведение:
- Бот продолжает принимать события, но **не размещает** новых ордеров за пределами `[P_low, P_high]`.
- Лог уровня WARNING: `price_out_of_range=true`, текущая mark price, границы. Mark price берется из `get_prices`. citeturn19view3  

Ручные шаги оператора:
- решить: остановить бота, пересоздать сетку (новый `P0`), либо расширить диапазон в конфиге и перезапустить.

### Ошибки API, разрывы сети, падение WebSocket
- На `3001 RateLimit` — экспоненциальная задержка (чисто реализационно), но конкретная рекомендация backoff в доке не найдена; важно иметь троттлинг согласно лимитам Place Order. citeturn5view2turn11view1turn5view1  
- На WS‑drop: переподключение; учитывать лимиты соединений (100 на IP) и лимиты аутентифицированных соединений (5 на wallet). citeturn26view1turn10view1  
- Поддерживать ping‑кадры каждые 30 секунд и учитывать ограничение 12 часов на соединение. citeturn1view6  

### Если обнаружена неожиданная taker‑комиссия или taker‑исполнение
- Событие `fill` содержит `is_taker` и `fee`. citeturn9view0  
Детерминированное правило:
- `is_taker=true` → CRITICAL log + `PAUSED`.

### Схема логов и список критических алертов
Минимальная схема (ключи):
- `ts`, `level`, `event`, `product_id`, `order_digest`, `k`, `side`, `price_x18`, `qty_x18`, `is_taker`, `fee_x18`, `err_code`.  
Поля `order_digest`, `is_taker`, `fee` документированы в fill‑событии. citeturn9view0  

**CRITICAL алерты:**
- `fill.is_taker=true`. citeturn9view0  
- повторяющиеся `3001 RateLimit` без прогресса. citeturn5view2  
- WS reconnect storm + приближение к лимиту соединений. citeturn26view1turn1view6  
- систематические `2005 InvalidPriceIncrement` / `2004 InvalidAmountIncrement` (ошибка округления). citeturn5view2turn7view0  

**H) Спецификация конфигурации**

### Пример конфига (YAML)

```yaml
network: mainnet

product:
  symbol: "BTC-PERP"          # подтверждено в Symbols
  product_id: 2               # подтверждено в Symbols

subaccount:
  name: "default"
  sender_hex: "НЕ_НАЙДЕНО_В_ДОКУМЕНТАЦИИ_КАК_ГОТОВОЕ_ПОЛЕ" 
  # В SDK примерах sender строится из owner+subaccount_name утилитами bytes32. 

grid:
  price_reference: "mark_price"   # требование
  grid_step_pct: 0.10
  lower_bound_pct: 5.0
  upper_bound_pct: 5.0
  levels_down: 20
  levels_up: 20
  order_size_x18: 10000000000000000   # пример x18; точный экономический смысл amount требует проверки
  no_action_outside_range: true       # требование

orders:
  order_type: "POST_ONLY"             # maker-only
  reduce_only_on_sells: true          # long-only защита
  spot_leverage: false                # трактовка как запрет borrow; перп-leverage=1x не найдено
  client_order_id_enabled: true       # опционально; id возвращается в Fill/OrderUpdate событиях

fills:
  ws_enabled: true
  stream_type: "fill"
  subscribe_product_id: 2
  subscribe_product_id_nullable: false
  pause_on_is_taker: true

polling_fallback:
  enabled: true
  poll_interval_sec: 5

retries:
  max_retries: 5
  backoff_base_sec: 1

paths:
  log_path: "/var/log/nado_grid_bot/bot.log"
  state_path: "/var/lib/nado_grid_bot/state.sqlite"
```

Обоснование ключевых полей:
- `symbol/product_id` подтверждаются через Symbols query. citeturn5view0  
- `POST_ONLY` как maker‑only реализуется через appendix (order type = POST_ONLY). citeturn11view1turn15view0  
- `reduce_only` описан в order appendix. citeturn11view1turn4view2  
- Подписка на `fill` stream описана в Streams; схема fill‑события — в Events. citeturn26view0turn9view0  

### Шаблон `.env` для секретов

**Имена переменных окружения в документации Nado/SDK не заданы** → ниже предложена внутренняя конвенция (это важно отметить при аудите). citeturn25view0turn15view0  

```bash
# НЕ_НАЙДЕНО_В_ДОКУМЕНТАЦИИ: стандартные имена env-переменных
NADO_PRIVATE_KEY="0x................................................"
NADO_SUBACCOUNT_NAME="default"
```

## Неизвестные и не найдено в документации

**I) Полный список критичных неизвестных/пробелов**

- **Как явно задать «плечо 1x» для перп‑позиции** (в смысле классических CEX perpetual leverage) через Gateway/SDK: **не найдено в документации**; обнаружен только флаг `spot_leverage`, описанный как контроль borrow/заема. citeturn11view1turn22view0  
- **Точная семантика `amount` (base qty vs quote qty) для BTC‑PERP** и как это соотносится с `min_size`: в All Products `min_size` описан как «quote USDT0», но ошибка `OrderAmountTooSmall` сравнивает `min_size` с `abs(amount)` → конфликт, требует практической верификации. citeturn7view0turn5view2  
- **SDK‑поддержка batch‑выставления `place_orders`**: в Gateway docs endpoint есть, но в перечислении методов `MarketExecuteAPI` отдельного `place_orders` не видно → возможно требуется кастомный вызов/низкоуровневый execute. **Не найдено в документации SDK как готовый метод.** citeturn26view2turn16view0turn19view0  
- **Готовый WebSocket‑клиент Subscriptions внутри Python SDK**: в API Reference SDK не обнаружен раздел/класс, выполняющий `subscribe`/`authenticate` к Subscriptions endpoint. Следовательно, требуется внешняя WS‑реализация. **Не найдено в документации SDK.** citeturn16view0turn26view0  
- **«Validate‑only» / симуляция размещения ордера без выставления**: **не найдено в документации** Gateway/SDK. citeturn11view1turn26view2  
- **Явные рекомендации по retry/backoff** при `3001 RateLimit` и сетевых ошибках: **не найдено в документации** (есть коды и лимиты, но нет алгоритма). citeturn5view1turn5view2  
- **Доступность mark price в виде WebSocket stream**: в Subscriptions streams/Events перечислены `funding_rate`, `best_bid_offer`, `trade`, `fill`, но отдельного события «mark price update» **не найдено**; mark price берется через indexer/SDK `get_prices`. citeturn26view0turn19view3  
- **Максимально допустимая дальность `order.expiration`** (секунды) и оптимальные TTL для «долгоживущих» сеточных ордеров: **не найдено в документации**; известно лишь, что expiration существует и задается в секундах. citeturn11view1turn22view0