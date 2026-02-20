# Быстрый старт

## 1. Замените ключ в `.env`

```bash
nano .env
# Замените NADO_PRIVATE_KEY на ваш реальный ключ
```

## 2. Проверьте подключение

```bash
source .venv/bin/activate
PYTHONPATH=nado-python-sdk:. python test_connection.py
```

## 3. Dry-run (безопасно, без реальных ордеров)

```bash
PYTHONPATH=nado-python-sdk:. python -m bot.cli dry-run
```

## 4. Запуск с минимальными параметрами (реальные ордера!)

Создайте `config.test.yaml`:
```yaml
grid:
  levels_down: 3      # Только 3 уровня
  levels_up: 3
  grid_step_pct: 0.5  # Больший шаг
```

Запустите:
```bash
PYTHONPATH=nado-python-sdk:. python -m bot.cli start --config config.test.yaml
```

Остановка: `Ctrl+C` (ордера останутся на бирже!)

## 5. Полный запуск

```bash
PYTHONPATH=nado-python-sdk:. python -m bot.cli start
```

---

**Подробная инструкция:** см. `TESTING.md`
